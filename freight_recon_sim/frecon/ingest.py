"""M-IMP: desensitized CSV import, hashing, formula-injection rejection, idempotency."""

import csv
import io
import os
import re

from . import conventions as C
from .audit_log import log_event
from .util import (
    ReconError,
    copy_bytes_new,
    mask_value,
    now_shanghai,
    pretty_json,
    read_json,
    sha256_bytes,
    sha256_obj,
    write_new_file,
)

PACKAGE_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DEFAULT_CATALOG = os.path.join(PACKAGE_DATA, f"{C.SIM_RULE_VERSION}.catalog.json")
DEFAULT_SNAPSHOT = os.path.join(PACKAGE_DATA, f"{C.SIM_RULE_VERSION}.snapshot.json")

_NEGATIVE_DECIMAL_RE = re.compile(r"^-\d+(\.\d+)?$")
_FORMULA_PREFIXES = ("=", "+", "@", "-", "\t", "\r")


def formula_prefix(value):
    """Return the offending prefix if a cell looks like a spreadsheet formula, else None.

    ENG-SIM boundary (TC-24): '-' is allowed only when the whole cell is a plain negative
    decimal such as -0.01. Leading spaces are ignored when checking the prefix.
    """
    if value is None:
        return None
    probe = value.lstrip(" ")
    if not probe:
        return None
    first = probe[0]
    if first not in _FORMULA_PREFIXES:
        return None
    if first == "-" and _NEGATIVE_DECIMAL_RE.match(probe):
        return None
    return {"\t": "TAB", "\r": "CR"}.get(first, first)


def _registered_header(table, name):
    return name in C.FIELD_WHITELIST[table] or name in C.HEADER_ALIASES


def parse_source(table, data, delimiter):
    """Decode and parse one CSV file. Raises ReconError with an IMP code on failure."""
    file_sha = sha256_bytes(data)
    base = {"source_table": table, "file_sha256": file_sha}
    if len(data) > C.MAX_FILE_BYTES:
        raise ReconError("IMP-006", {**base, "detail": "文件字节数超出 ENG-SIM 上限"}, C.EXIT_REFUSED)
    try:
        text = data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReconError(
            "IMP-001", {**base, "detail": f"无法按 UTF-8 解码（字节偏移 {exc.start}）"}, C.EXIT_REFUSED
        )
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise ReconError("IMP-002", {**base, "detail": f"CSV 无法解析：{exc}"}, C.EXIT_REFUSED)
    records = [r for r in records if r != []]
    if not records or len(records) < 2:
        raise ReconError("IMP-003", {**base, "detail": "空文件或无数据行"}, C.EXIT_REFUSED)
    header = records[0]
    if len(set(header)) != len(header):
        raise ReconError("IMP-002", {**base, "detail": "表头列名重复"}, C.EXIT_REFUSED)
    unregistered = [h for h in header if not _registered_header(table, h)]
    if unregistered:
        raise ReconError(
            "IMP-004", {**base, "detail": "表头含未登记字段", "unregistered_headers": unregistered},
            C.EXIT_REFUSED,
        )
    rows = records[1:]
    if len(rows) > C.MAX_ROWS:
        raise ReconError("IMP-006", {**base, "detail": "行数超出 ENG-SIM 上限"}, C.EXIT_REFUSED)
    for idx, row in enumerate(rows, start=2):
        if len(row) != len(header):
            raise ReconError(
                "IMP-002", {**base, "detail": f"第 {idx} 行列数与表头不一致"}, C.EXIT_REFUSED
            )
    findings = []
    for idx, row in enumerate(rows, start=2):
        for col, value in zip(header, row):
            prefix = formula_prefix(value)
            if prefix is not None:
                findings.append({
                    "file_line": idx, "column": col, "prefix": prefix,
                    "value_masked": mask_value(value),
                })
    if findings:
        raise ReconError(
            "IMP-005", {**base, "detail": "检出 CSV 公式注入形态，未执行任何单元格", "findings": findings},
            C.EXIT_REFUSED,
        )
    row_digests = [sha256_obj(row) for row in rows]
    return {
        "source_table": table,
        "file_sha256": file_sha,
        "bytes": len(data),
        "rows": len(rows),
        "header": header,
        "row_digests": row_digests,
    }


def read_source_rows(path, delimiter):
    with open(path, "rb") as fh:
        data = fh.read()
    text = data.decode("utf-8-sig", errors="strict")
    records = [r for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True) if r != []]
    return records[0], records[1:], sha256_bytes(data)


def _read_optional(path):
    if path is None:
        return None
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _reject(workdir, idempotency_key, err, file_shas):
    rejection_id = "IR-SIM-" + sha256_obj([idempotency_key, err.code, file_shas])[:16]
    record = {
        "rejection_id": rejection_id,
        "ingest_status": "rejected",
        "idempotency_key": idempotency_key,
        "block_scope": C.SCOPE_BATCH,
        "error": {"code": err.code, "meaning": C.CODES.get(err.code), "detail": err.detail},
        "file_sha256": file_shas,
        "reconcile_allowed": False,
        "data_label": C.DATA_LABEL,
    }
    path = os.path.join(workdir, "rejections", f"{rejection_id}.json")
    replayed = os.path.exists(path)
    if not replayed:
        write_new_file(path, pretty_json(record))
    log_event(workdir, "import_rejected", rejection_id=rejection_id, code=err.code,
              exit_code=err.exit_code, idempotency_key=idempotency_key, replayed=replayed)
    record["replayed"] = replayed
    return record, err.exit_code


def import_batch(workdir, bill_path, waybill_path, idempotency_key, declared_encoding,
                 declared_delimiter, desensitized, rules_path=None, snapshot_path=None):
    if not idempotency_key:
        raise ReconError("SYS-001", "idempotency_key 必填", C.EXIT_USAGE)
    file_shas = {}
    raw = {}
    for table, path in ((C.TABLE_BILL, bill_path), (C.TABLE_WAYBILL, waybill_path)):
        data = _read_optional(path)
        if data is None:
            raise ReconError("SYS-001", f"无法读取 {table} 源文件", C.EXIT_USAGE)
        raw[table] = data
        file_shas[table] = sha256_bytes(data)
    try:
        if desensitized is not True:
            raise ReconError("IMP-008", {"detail": "source_declared_desensitized 必须为 true"}, C.EXIT_REFUSED)
        if (declared_encoding or "").upper() not in C.ALLOWED_ENCODINGS:
            raise ReconError(
                "IMP-001", {"detail": f"声明编码不在首期范围（仅 UTF-8），收到声明：{declared_encoding}"},
                C.EXIT_REFUSED,
            )
        if declared_delimiter not in C.ALLOWED_DELIMITERS:
            raise ReconError("IMP-002", {"detail": "声明分隔符不在 comma/tab/semicolon 内"}, C.EXIT_REFUSED)
        delimiter = C.ALLOWED_DELIMITERS[declared_delimiter]
        parsed = {t: parse_source(t, raw[t], delimiter) for t in C.SOURCE_TABLES}
    except ReconError as err:
        return _reject(workdir, idempotency_key, err, file_shas)

    if rules_path is None:
        rules_path, snapshot_path = DEFAULT_CATALOG, DEFAULT_SNAPSHOT
    catalog_bytes = _read_optional(rules_path)
    snapshot_bytes = _read_optional(snapshot_path)
    catalog_sha = sha256_bytes(catalog_bytes) if catalog_bytes is not None else None
    snapshot_sha = sha256_bytes(snapshot_bytes) if snapshot_bytes is not None else None

    file_digests = [
        {k: parsed[t][k] for k in ("source_table", "file_sha256", "bytes", "rows")} for t in C.SOURCE_TABLES
    ]
    input_bundle_sha = sha256_obj([[d["source_table"], d["file_sha256"]] for d in file_digests])
    batch_id = "IB-SIM-" + sha256_obj([idempotency_key, input_bundle_sha, catalog_sha, snapshot_sha])[:16]

    idem_path = os.path.join(workdir, "idempotency", sha256_bytes(idempotency_key.encode("utf-8"))[:32] + ".json")
    if os.path.exists(idem_path):
        prior = read_json(idem_path)
        same = (
            prior["input_bundle_sha256"] == input_bundle_sha
            and prior["rule_catalog_sha256"] == catalog_sha
            and prior["rule_snapshot_file_sha256"] == snapshot_sha
        )
        if not same:
            err = ReconError("IMP-007", {
                "detail": "同幂等键但输入包或规则目录哈希不同，拒绝覆盖；既有批次保持不变",
                "existing_import_batch_id": prior["import_batch_id"],
                "existing_input_bundle_sha256": prior["input_bundle_sha256"],
                "new_input_bundle_sha256": input_bundle_sha,
            }, C.EXIT_REFUSED)
            return _reject(workdir, idempotency_key, err, file_shas)
        batch = read_json(os.path.join(workdir, "batches", prior["import_batch_id"], "batch.json"))
        log_event(workdir, "import_replayed", import_batch_id=batch["import_batch_id"],
                  input_bundle_sha256=input_bundle_sha, replayed=True, idempotency_key=idempotency_key)
        return {**_public(batch), "replayed": True}, C.EXIT_OK

    batch_dir = os.path.join(workdir, "batches", batch_id)
    inputs_dir = os.path.join(batch_dir, "inputs")
    for table in C.SOURCE_TABLES:
        copy_bytes_new(raw[table], os.path.join(inputs_dir, f"{table}.csv"))
    if catalog_bytes is not None:
        copy_bytes_new(catalog_bytes, os.path.join(inputs_dir, "rule_catalog.json"))
    if snapshot_bytes is not None:
        copy_bytes_new(snapshot_bytes, os.path.join(inputs_dir, "rule_snapshot.json"))

    batch = {
        "import_batch_id": batch_id,
        "ingest_status": "accepted",
        "idempotency_key": idempotency_key,
        "declared_encoding": "UTF-8",
        "declared_delimiter": declared_delimiter,
        "source_declared_desensitized": True,
        "file_digests": file_digests,
        "input_bundle_sha256": input_bundle_sha,
        "headers": {t: parsed[t]["header"] for t in C.SOURCE_TABLES},
        "row_digests": {t: parsed[t]["row_digests"] for t in C.SOURCE_TABLES},
        "rule_catalog_sha256": catalog_sha,
        "rule_catalog_status": "present" if catalog_bytes is not None else "unloadable_or_missing",
        "rule_snapshot_file_sha256": snapshot_sha,
        "rule_snapshot_status": "present" if snapshot_bytes is not None else "missing",
        "imported_at": now_shanghai(),
        "engine_version": C.ENGINE_VERSION,
        "eng_sim_version": C.ENG_SIM_VERSION,
        "data_label": C.DATA_LABEL,
    }
    write_new_file(os.path.join(batch_dir, "batch.json"), pretty_json(batch))
    write_new_file(idem_path, pretty_json({
        "idempotency_key": idempotency_key,
        "input_bundle_sha256": input_bundle_sha,
        "rule_catalog_sha256": catalog_sha,
        "rule_snapshot_file_sha256": snapshot_sha,
        "import_batch_id": batch_id,
    }))
    log_event(workdir, "import_accepted", import_batch_id=batch_id, input_bundle_sha256=input_bundle_sha,
              rule_catalog_sha256=catalog_sha, idempotency_key=idempotency_key, replayed=False)
    return {**_public(batch), "replayed": False}, C.EXIT_OK


def _public(batch):
    keys = ("import_batch_id", "ingest_status", "idempotency_key", "file_digests", "input_bundle_sha256",
            "rule_catalog_sha256", "rule_catalog_status", "rule_snapshot_status", "headers")
    return {k: batch[k] for k in keys}


def load_batch(workdir, batch_id):
    path = os.path.join(workdir, "batches", batch_id or "-", "batch.json")
    if not batch_id or not os.path.exists(path):
        raise ReconError("EXP-001", "批次不存在或导入未被接受", C.EXIT_REFUSED)
    return read_json(path)

