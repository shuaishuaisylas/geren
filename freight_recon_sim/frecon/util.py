import hashlib
import json
import os
import re
from datetime import datetime
from decimal import (
    ROUND_HALF_UP,
    Decimal,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    localcontext,
)

from .conventions import TZ_SHANGHAI

_DECIMAL_RE = re.compile(r"^-?\d+(\.\d+)?$")
_NON_NEGATIVE_DECIMAL_RE = re.compile(r"^\d+(\.\d+)?$")


class ReconError(Exception):
    """A refusal or batch-level failure carrying a registered code."""

    def __init__(self, code, detail, exit_code, payload=None):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.exit_code = exit_code
        self.payload = payload or {}


def parse_decimal(text, allow_negative=True, max_scale=None):
    """Parse a plain decimal string. Rejects floats, exponents, NaN/Infinity, separators."""
    if text is None:
        return None
    pattern = _DECIMAL_RE if allow_negative else _NON_NEGATIVE_DECIMAL_RE
    if not pattern.match(text):
        return None
    if max_scale is not None and "." in text and len(text.split(".", 1)[1]) > max_scale:
        return None
    return Decimal(text)


def exact_mul(*values):
    """Exact decimal product; raises if any precision would be lost."""
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        ctx.traps[InvalidOperation] = True
        ctx.traps[Overflow] = True
        result = Decimal(1)
        for value in values:
            result = result * value
        return result


def exact_add(a, b):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        return a + b


def exact_sub(a, b):
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        return a - b


def round_half_up(value, scale):
    with localcontext() as ctx:
        ctx.prec = 80
        return value.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)


def dec_str(value):
    return None if value is None else format(value, "f")


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    with open(path, "rb") as fh:
        return sha256_bytes(fh.read())


def canonical_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_obj(obj):
    return sha256_bytes(canonical_json(obj).encode("utf-8"))


def pretty_json(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def now_shanghai():
    return datetime.now(TZ_SHANGHAI).isoformat(timespec="seconds")


def write_new_file(path, text, readonly=False):
    """Write a file that must not exist yet (no silent overwrite)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    if readonly:
        os.chmod(path, 0o444)


def copy_bytes_new(data, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o444)


def read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_float(v) for v in obj)
    return False


class SensitiveGuard:
    """Blocks any observable output that contains a raw value from a sensitive column."""

    MIN_LEN = 6

    def __init__(self, values=()):
        candidates = set()
        for value in values:
            if not value:
                continue
            candidates.add(value.strip())
            candidates.update(value.split())
        self.values = sorted(v for v in candidates if len(v) >= self.MIN_LEN)

    def leaks(self, text):
        return [v for v in self.values if v in text]

    def check(self, text, where):
        if self.leaks(text):
            from .conventions import EXIT_SECURITY

            raise ReconError("SEC-001", f"敏感原文出现在可观察输出（{where}），已拒绝写出", EXIT_SECURITY)
        return text


def mask_value(value):
    """Non-reversible label for a rejected or sensitive cell value."""
    if value is None:
        return None
    return f"<masked len={len(value)} sha256:{sha256_bytes(value.encode('utf-8'))[:12]}>"
