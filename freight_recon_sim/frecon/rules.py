"""M-RUL: immutable rule snapshot and closed-set formula registry (no eval)."""

import json
import os
import re
from decimal import Decimal

from . import conventions as C
from .util import exact_add, exact_mul, parse_decimal, sha256_file

# Closed registry. formula_expr in a catalog is only compared as text against these
# strings; it is never parsed or evaluated.
FORMULAS = {
    "(1)": {"expr": "A = ROUND_HALF_UP(P × W, 2)", "inputs": ["P", "W"]},
    "(2)": {"expr": "A = ROUND_HALF_UP(A_FR001 × R, 2)", "inputs": ["A_FR001", "R"]},
    "(3)": {"expr": "A = ROUND_HALF_UP(P × W × (1 + R), 2)", "inputs": ["P", "W", "R"]},
}
BASE_SYMBOL_RE = re.compile(r"^A_([A-Z0-9]+)$")

RULE_KEYS = (
    "rule_id", "rule_version", "charge_code", "formula_id", "formula_expr", "required_inputs",
    "base_amount_def", "rounding_mode", "rounding_scale", "rounding_layer", "tolerance_abs",
    "tolerance_type", "currency", "tax_included_flag", "approval_status", "effective_from",
    "effective_to", "evidence_required",
)


def apply_formula(formula_id, values):
    """Return (intermediates, unrounded) using exact decimal operations only."""
    if formula_id == "(1)":
        pw = exact_mul(values["P"], values["W"])
        return {"P×W": pw}, pw
    if formula_id == "(2)":
        base = next(v for k, v in values.items() if k.startswith("A_"))
        ar = exact_mul(base, values["R"])
        return {"A_FR001×R": ar}, ar
    if formula_id == "(3)":
        pw = exact_mul(values["P"], values["W"])
        one_r = exact_add(Decimal(1), values["R"])
        total = exact_mul(pw, one_r)
        return {"P×W": pw, "1+R": one_r, "P×W×(1+R)": total}, total
    raise KeyError(formula_id)


def _schema_problems(catalog):
    problems = []
    if not isinstance(catalog, dict) or not isinstance(catalog.get("rules"), list):
        return ["目录缺少 rules 列表"]
    version = catalog.get("rule_version")
    if not version:
        problems.append("目录缺少 rule_version")
    seen = set()
    for idx, rule in enumerate(catalog["rules"]):
        missing = [k for k in RULE_KEYS if k not in rule]
        if missing:
            problems.append(f"rules[{idx}] 缺少登记项 {missing}")
            continue
        if rule["rule_version"] != version:
            problems.append(f"rules[{idx}] rule_version 与目录版本不一致")
        if rule["charge_code"] in seen:
            problems.append(f"rules[{idx}] charge_code 重复登记")
        seen.add(rule["charge_code"])
        if not str(rule["approval_status"]).startswith(C.SIM_APPROVAL_STATUS):
            problems.append(f"rules[{idx}] approval_status 非「模拟约定」，不在原型范围")
        if rule["rounding_mode"] not in C.ALLOWED_ROUNDING_MODES:
            problems.append(f"rules[{idx}] rounding_mode 未登记")
        if not isinstance(rule["rounding_scale"], int) or isinstance(rule["rounding_scale"], bool):
            problems.append(f"rules[{idx}] rounding_scale 必须为整数")
        if parse_decimal(str(rule["tolerance_abs"]), allow_negative=False) is None:
            problems.append(f"rules[{idx}] tolerance_abs 必须为非负十进制字符串")
    return problems


def load_snapshot(inputs_dir, batch):
    """Load the frozen catalog copy. Returns dict with status ok / LOAD / UNFROZEN."""
    catalog_path = os.path.join(inputs_dir, "rule_catalog.json")
    snapshot_path = os.path.join(inputs_dir, "rule_snapshot.json")
    if not os.path.exists(catalog_path):
        return {"status": "LOAD", "detail": "规则目录缺失或不可读"}
    try:
        with open(catalog_path, "r", encoding="utf-8") as fh:
            catalog = json.load(fh)
    except (ValueError, OSError) as exc:
        return {"status": "LOAD", "detail": f"规则目录无法解析：{type(exc).__name__}"}
    problems = _schema_problems(catalog)
    if problems:
        return {"status": "LOAD", "detail": "规则目录不是合法登记目录", "problems": problems}
    catalog_sha = sha256_file(catalog_path)
    if catalog_sha != batch["rule_catalog_sha256"]:
        return {"status": "LOAD", "detail": "目录副本哈希与导入记录不一致"}
    if not os.path.exists(snapshot_path):
        return {"status": "UNFROZEN", "detail": "无冻结快照（缺 rule_snapshot_id 与冻结哈希）",
                "catalog": catalog, "rule_catalog_sha256": catalog_sha}
    try:
        with open(snapshot_path, "r", encoding="utf-8") as fh:
            snapshot = json.load(fh)
    except (ValueError, OSError):
        return {"status": "UNFROZEN", "detail": "冻结快照无法解析", "catalog": catalog,
                "rule_catalog_sha256": catalog_sha}
    if not snapshot.get("rule_snapshot_id") or not snapshot.get("rule_catalog_sha256"):
        return {"status": "UNFROZEN", "detail": "快照缺 rule_snapshot_id 或 rule_catalog_sha256",
                "catalog": catalog, "rule_catalog_sha256": catalog_sha}
    if snapshot["rule_catalog_sha256"] != catalog_sha or snapshot.get("rule_version") != catalog["rule_version"]:
        return {"status": "UNFROZEN", "detail": "目录内容或版本与冻结快照不符，视为未冻结",
                "catalog": catalog, "rule_catalog_sha256": catalog_sha}
    return {"status": "ok", "catalog": catalog, "snapshot": snapshot, "rule_catalog_sha256": catalog_sha}


def waybill_required_fields(catalog, charge_codes=None):
    """Input columns required by the rules the batch actually references (all rules if unknown)."""
    fields = []
    for rule in catalog.get("rules", []) if catalog else []:
        if charge_codes is not None and rule["charge_code"] not in charge_codes:
            continue
        for symbol in rule.get("required_inputs", []):
            field = C.INPUT_FIELDS.get(symbol)
            if field and field not in fields:
                fields.append(field)
    return fields


def rule_problems(rule):
    """Row-level rule checks. Returns list of (code, detail)."""
    reasons = []
    if rule.get("effective_from") is not None or rule.get("effective_to") is not None:
        reasons.append(("CAL-001", "规则设有有效期，账期对齐依据待业务确认，原型不自动命中"))
        return reasons
    fid = rule.get("formula_id")
    registered = FORMULAS.get(fid)
    if registered is None:
        reasons.append(("RUL-001", f"formula_id={fid} 不在已登记闭集 (1)(2)(3)"))
    expr = rule.get("formula_expr")
    if expr is not None and (registered is None or expr != registered["expr"]):
        reasons.append(("RUL-002", "formula_expr 与已登记表达式不一致，拒绝求值（未执行）"))
    if registered is not None and list(rule.get("required_inputs", [])) != registered["inputs"]:
        reasons.append(("RUL-001", "required_inputs 与已登记公式不一致"))
    return reasons


def base_charge_code(rule):
    for symbol in rule.get("required_inputs", []):
        m = BASE_SYMBOL_RE.match(symbol)
        if m:
            return m.group(1), symbol
    return None, None
