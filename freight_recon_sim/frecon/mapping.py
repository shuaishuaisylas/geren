"""M-VAL (FR-MAP): mapping proposals and the human confirmation gate."""

import os
import re

from . import conventions as C
from .audit_log import log_event
from .util import ReconError, pretty_json, read_json, sha256_obj, write_new_file


def compute_mapping_hash(batch_id, headers, mapping_version, tables):
    return sha256_obj({
        "import_batch_id": batch_id,
        "headers": headers,
        "mapping_version": mapping_version,
        "tables": tables,
    })


def _propose_table(table, header):
    proposal, ambiguous = {}, {}
    for col in header:
        if col in C.FIELD_WHITELIST[table]:
            proposal[col] = col
        else:
            candidates = [c for c in C.HEADER_ALIASES.get(col, []) if c in C.FIELD_WHITELIST[table]]
            if len(candidates) == 1:
                proposal[col] = candidates[0]
            else:
                proposal[col] = None
                ambiguous[col] = candidates
    return proposal, ambiguous


def required_columns(table, waybill_required_fields):
    if table == C.TABLE_BILL:
        return list(C.REQUIRED_BILL_COLUMNS)
    return list(C.REQUIRED_WAYBILL_KEY_COLUMNS) + list(waybill_required_fields)


def column_presence(tables, waybill_required_fields):
    presence = {}
    for table in C.SOURCE_TABLES:
        targets = {t for t in tables[table].values() if t}
        required = required_columns(table, waybill_required_fields)
        presence[table] = {
            "required": required,
            "present": [r for r in required if r in targets],
            "missing_columns": [r for r in required if r not in targets],
        }
    return presence


def validate(workdir, batch, waybill_required_fields, ai_proposal=None):
    """Produce mapping proposals (status=proposed). Never confirms anything."""
    headers = batch["headers"]
    proposals = []
    tables, ambiguous = {}, {}
    for table in C.SOURCE_TABLES:
        tables[table], ambiguous[table] = _propose_table(table, headers[table])
    sys_hash = compute_mapping_hash(batch["import_batch_id"], headers, None, tables)
    proposals.append({
        "mapping_id": "MP-" + sys_hash[:16],
        "mapping_status": "proposed",
        "proposed_by": f"system header dictionary {C.ALIAS_DICT_VERSION}",
        "tables": tables,
        "ambiguous_columns": ambiguous,
        "mapping_hash": sys_hash,
    })
    if ai_proposal is not None:
        ai_tables = ai_proposal.get("tables", {})
        ai_hash = compute_mapping_hash(batch["import_batch_id"], headers, ai_proposal.get("candidate_id"), ai_tables)
        proposals.append({
            "mapping_id": "MP-" + ai_hash[:16],
            "mapping_status": "proposed",
            "proposed_by": f"AI candidate supplied offline ({ai_proposal.get('candidate_id')}); not confirmed",
            "tables": ai_tables,
            "ambiguous_columns": {},
            "mapping_hash": ai_hash,
        })
    report = {
        "import_batch_id": batch["import_batch_id"],
        "validation_status": "proposals_only_mapping_unconfirmed",
        "column_presence_under_system_proposal": column_presence(tables, waybill_required_fields),
        "mapping_proposals": proposals,
        "note": ("映射建议仅为 proposed；未经人工确认不得进入自动匹配与计算（FR-MAP / MAP-001）。"
                 "列存在性为预览（按目录全部规则输入计）；以确认映射后核对时按本批实际收费项判定为准。"),
        "data_label": C.DATA_LABEL,
    }
    path = os.path.join(workdir, "batches", batch["import_batch_id"], "validation",
                        f"{proposals[-1]['mapping_id']}.json")
    if not os.path.exists(path):
        write_new_file(path, pretty_json(report))
    log_event(workdir, "validated", import_batch_id=batch["import_batch_id"],
              mapping_id=proposals[-1]["mapping_id"])
    return report


def _is_non_human(confirmed_by):
    upper = confirmed_by.strip().upper()
    return any(upper == p or upper.startswith(p + "-") or upper.startswith(p + ":") or upper.startswith(p + "_")
               or upper.startswith(p + " ") for p in C.NON_HUMAN_CONFIRMER_PREFIXES)


def confirm(workdir, batch, mapping_doc, confirmed_by, confirmed_at):
    batch_id = batch["import_batch_id"]
    confirmed_by = (confirmed_by or "").strip()
    if not confirmed_by or _is_non_human(confirmed_by):
        log_event(workdir, "mapping_confirm_refused", import_batch_id=batch_id, code="MAP-002")
        raise ReconError("MAP-002", "确认人必须为人工；AI、系统或自动身份不得确认映射", C.EXIT_REFUSED)
    problems = []
    if not re.match(C.CONFIRMED_AT_PATTERN, confirmed_at or ""):
        problems.append("confirmed_at 必须为 Asia/Shanghai 时间并带 +08:00 偏移")
    version = mapping_doc.get("mapping_version")
    if not version:
        problems.append("mapping_version 必填")
    tables = mapping_doc.get("tables") or {}
    if sorted(tables) != sorted(C.SOURCE_TABLES):
        problems.append("tables 必须且仅包含 SIM_BILL 与 SIM_WAYBILL")
    else:
        for table in C.SOURCE_TABLES:
            header = batch["headers"][table]
            mapped = tables[table]
            if sorted(mapped) != sorted(header):
                problems.append(f"{table} 映射必须逐一覆盖本批全部源列")
            targets = list(mapped.values())
            bad = [t for t in targets if t not in C.FIELD_WHITELIST[table]]
            if bad:
                problems.append(f"{table} 目标字段不在白名单：{bad}")
            if len(set(targets)) != len(targets):
                problems.append(f"{table} 存在多个源列映射到同一目标字段")
    if problems:
        log_event(workdir, "mapping_confirm_refused", import_batch_id=batch_id, code="MAP-003")
        raise ReconError("MAP-003", problems, C.EXIT_REFUSED)

    mapping_hash = compute_mapping_hash(batch_id, batch["headers"], version, tables)
    record = {
        "import_batch_id": batch_id,
        "mapping_id": "MC-" + mapping_hash[:16],
        "mapping_status": "confirmed",
        "mapping_version": version,
        "mapping_hash": mapping_hash,
        "headers": batch["headers"],
        "tables": tables,
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
        "confirmation_label": (
            "模拟确认记录（SIM- 前缀，非真实人员）" if confirmed_by.upper().startswith("SIM-")
            else "人工确认（确认人由调用方声明，原型不做身份鉴权）"
        ),
    }
    path = os.path.join(workdir, "batches", batch_id, "mapping", f"{mapping_hash}.json")
    replayed = os.path.exists(path)
    if not replayed:
        write_new_file(path, pretty_json(record))
    log_event(workdir, "mapping_confirmed", import_batch_id=batch_id, mapping_id=record["mapping_id"],
              mapping_hash=mapping_hash, replayed=replayed)
    return {k: record[k] for k in ("import_batch_id", "mapping_id", "mapping_status", "mapping_version",
                                   "mapping_hash", "confirmed_by", "confirmed_at", "confirmation_label")}


def load_confirmed(workdir, batch, mapping_hash):
    """Return the confirmed mapping or raise MAP-001."""
    batch_id = batch["import_batch_id"]
    if not mapping_hash or not re.match(r"^[0-9a-f]{64}$", mapping_hash):
        raise ReconError("MAP-001", "未提供已确认映射的 mapping_hash", C.EXIT_REFUSED)
    path = os.path.join(workdir, "batches", batch_id, "mapping", f"{mapping_hash}.json")
    if not os.path.exists(path):
        raise ReconError("MAP-001", "该 mapping_hash 无人工确认记录（仅有建议或未确认）", C.EXIT_REFUSED)
    record = read_json(path)
    recomputed = compute_mapping_hash(batch_id, batch["headers"], record.get("mapping_version"),
                                      record.get("tables"))
    if (record.get("mapping_status") != "confirmed" or record.get("mapping_hash") != mapping_hash
            or recomputed != mapping_hash or record.get("headers") != batch["headers"]):
        raise ReconError("MAP-001", "已确认映射内容被变更而未重新确认，原确认失效", C.EXIT_REFUSED)
    return record
