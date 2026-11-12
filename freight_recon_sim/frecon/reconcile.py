"""M-MAT + M-CAL: gates, exact matching, deterministic decimal calculation and status."""

import os
import re

from . import conventions as C
from .ai_boundary import explanation_placeholder
from .audit_log import log_event
from .ingest import load_batch, read_source_rows
from .mapping import column_presence, load_confirmed
from .rules import apply_formula, base_charge_code, load_snapshot, rule_problems, waybill_required_fields
from .util import (
    ReconError,
    SensitiveGuard,
    dec_str,
    exact_sub,
    parse_decimal,
    pretty_json,
    read_json,
    round_half_up,
    sha256_bytes,
    sha256_obj,
    write_new_file,
)

_PERIOD_RE = re.compile(C.PERIOD_PATTERN)


def _reason(code, detail):
    return {"code": code, "meaning": C.CODES[code], "detail": detail}


def status_for(codes):
    for group, status in C.ROW_PRIORITY:
        if codes & group:
            return status
    return C.ST_PASS


def exception_type_for(codes):
    if "REC-011" in codes:
        return C.EX_DUP
    if "REC-012" in codes:
        return C.EX_DOC_MISSING
    if "REC-013" in codes:
        return C.EX_MISSED
    if "CAL-005" in codes:
        return C.EX_AMOUNT
    return C.EX_OTHER if codes else C.EX_NONE


def completeness_for(codes):
    if "VAL-014" in codes:
        return C.CF_KEY_INCOMPLETE
    if codes & {"REC-012", "REC-013"}:
        return C.CF_NO_COUNTERPART
    if codes & {"CAL-002", "CAL-003"}:
        return C.CF_MISSING_RULE_INPUT
    return C.CF_COMPLETE


class Row:
    def __init__(self, table, file_line, values, row_sha, source_row_id):
        self.table = table
        self.file_line = file_line
        self.values = values
        self.row_sha = row_sha
        self.source_row_id = source_row_id
        self.ref = f"{table}.{source_row_id}"

    def get(self, field):
        value = self.values.get(field)
        return value.strip() if value is not None else ""


def _load_rows(batch, batch_dir, mapping):
    delimiter = C.ALLOWED_DELIMITERS[batch["declared_delimiter"]]
    rows, integrity, sensitive_values = {}, {}, []
    digests = {d["source_table"]: d["file_sha256"] for d in batch["file_digests"]}
    for table in C.SOURCE_TABLES:
        header, raw_rows, file_sha = read_source_rows(os.path.join(batch_dir, "inputs", f"{table}.csv"), delimiter)
        row_shas = [sha256_obj(r) for r in raw_rows]
        integrity[table] = {
            "file_sha256_at_import": digests[table],
            "file_sha256_at_run": file_sha,
            "file_unchanged": file_sha == digests[table],
            "row_digests_unchanged": row_shas == batch["row_digests"][table],
        }
        colmap = mapping["tables"][table]
        table_rows = []
        for idx, (raw, row_sha) in enumerate(zip(raw_rows, row_shas), start=2):
            values = {colmap[col]: val for col, val in zip(header, raw)}
            for field in C.SENSITIVE_FIELDS:
                if values.get(field):
                    sensitive_values.append(values[field])
            rid = (values.get("source_row_id") or "").strip() or f"L{idx}"
            table_rows.append(Row(table, idx, values, row_sha, rid))
        rows[table] = table_rows
    return rows, integrity, SensitiveGuard(sensitive_values)


def _derived_copy(batch_id, table, rows, integrity):
    transform_log, out_rows = [], []
    for row in rows:
        clean = {}
        for field, value in sorted(row.values.items()):
            if field in C.SENSITIVE_FIELDS:
                continue
            stripped = value.strip()
            if stripped != value:
                transform_log.append({"source_row_id": row.source_row_id, "field": field,
                                      "transform": "strip_whitespace"})
            clean[field] = stripped
        out_rows.append({"source_row_id": row.source_row_id, "file_line": row.file_line,
                         "source_row_sha256": row.row_sha, "values": clean})
    return {
        "derived_copy_id": f"DRV-{batch_id}-{table}",
        "derived_version": "v1",
        "lineage": {"import_batch_id": batch_id, "source_table": table,
                    "source_file_sha256": integrity[table]["file_sha256_at_import"]},
        "note": "派生副本：仅在此副本上规范化；源文件只读未改写；敏感字段不进入派生副本。",
        "transform_log": transform_log,
        "rows": out_rows,
    }


class Engine:
    def __init__(self, batch_id, catalog, rows):
        self.batch_id = batch_id
        self.catalog = catalog
        self.rule_version = catalog["rule_version"]
        self.rules = {r["charge_code"]: r for r in catalog["rules"]}
        self.rows = rows
        self.calc_cache = {}
        self.status_by_key = {}

    # ----- calculation -----
    def calc(self, wb, base=None):
        cache_key = (wb.ref, base is not None)
        if cache_key in self.calc_cache:
            return self.calc_cache[cache_key]
        result = self._calc(wb, base)
        self.calc_cache[cache_key] = result
        return result

    def _calc(self, wb, base):
        charge_code = wb.get("charge_code")
        rule = self.rules.get(charge_code)
        out = {"source_row_id": wb.ref, "inputs": {}, "missing_fields": [], "intermediates": {},
               "output_amount": None, "reasons": [], "base_amount_source": None}
        if rule is None:
            out["reasons"].append(_reason("CAL-001", f"收费项 {charge_code} 未在 {self.rule_version} 命中；禁止套用其他收费项公式"))
            return out
        problems = rule_problems(rule)
        if problems:
            out["reasons"].extend(_reason(code, detail) for code, detail in problems)
            return out
        values = {}
        for symbol in rule["required_inputs"]:
            if symbol.startswith("A_"):
                if base is None or base.get("value") is None:
                    out["reasons"].append(_reason("CAL-003", (base or {}).get("detail", "附加费基数不可用")))
                    out["inputs"][symbol] = {"value": None, "source": (base or {}).get("source"), "status": "不可用"}
                    out["missing_fields"].append(symbol)
                else:
                    values[symbol] = base["value"]
                    out["inputs"][symbol] = {"value": dec_str(base["value"]), "source": base["source"],
                                             "rounded": True}
                    out["base_amount_source"] = base["source"]
                continue
            field = C.INPUT_FIELDS[symbol]
            raw = wb.get(field)
            entry = {"field": field, "source": wb.ref}
            if raw == "":
                entry.update(value=None, status="缺失")
                out["missing_fields"].append(field)
            else:
                parsed = parse_decimal(raw, allow_negative=False)
                if parsed is None:
                    entry.update(value=None, status="不可解析")
                    out["reasons"].append(_reason("VAL-010", f"{wb.ref} 字段 {field} 不是合法非负十进制字符串"))
                else:
                    entry.update(value=raw)
                    values[symbol] = parsed
            out["inputs"][symbol] = entry
        non_base_missing = [f for f in out["missing_fields"] if not f.startswith("A_")]
        if non_base_missing:
            out["reasons"].append(_reason("CAL-002", f"{wb.ref} 缺少规则必填输入 {non_base_missing}；禁止替算或用账单额反推"))
        uom = wb.get("weight_uom")
        if "W" in rule["required_inputs"] and uom and uom != C.SIM_WEIGHT_UOM:
            out["reasons"].append(_reason("CAL-004", f"重量单位 {uom} 与模拟约定 kg 不一致，禁止暗含换算"))
        if out["reasons"]:
            return out
        intermediates, unrounded = apply_formula(rule["formula_id"], values)
        out["intermediates"] = {k: dec_str(v) for k, v in intermediates.items()}
        out["output_amount"] = round_half_up(unrounded, rule["rounding_scale"])
        return out

    def base_for(self, waybill_no, rule, wb_by_key):
        base_code, _symbol = base_charge_code(rule)
        candidates = wb_by_key.get((waybill_no, base_code), [])
        if len(candidates) != 1:
            return {"value": None, "source": None,
                    "detail": f"同运单 {base_code} 运单行数={len(candidates)}，无法得出唯一已舍入基数"}, None
        base_row = candidates[0]
        base_rule = self.rules.get(base_code)
        if base_rule is None or base_charge_code(base_rule)[0] is not None:
            return {"value": None, "source": base_row.ref, "detail": f"基础收费项 {base_code} 规则不可用"}, None
        base_calc = self.calc(base_row)
        if base_calc["output_amount"] is None:
            return {"value": None, "source": base_row.ref,
                    "detail": f"基础行 {base_row.ref} 无法计算已舍入金额"}, None
        statuses = self.status_by_key.get((waybill_no, base_code), [])
        source = f"{base_row.ref} 的 {base_code} 已舍入行金额（基础行状态：{'/'.join(statuses) or '无'}）"
        if C.ST_PENDING in statuses:
            return {"value": None, "source": source, "detail": f"基础行 {base_code} 待人工确认，基数不可用"}, None
        extra = None
        if any(s != C.ST_PASS for s in statuses):
            extra = _reason("CAL-006", f"基础行 {base_code} 状态为 {'/'.join(statuses)}；附加费仅计算供证据")
        return {"value": base_calc["output_amount"], "source": source}, extra

    # ----- result assembly -----
    def _item(self, row, rule, reasons, **extra):
        codes = {r["code"] for r in reasons}
        status = status_for(codes)
        item = {
            "line_uid": f"{self.batch_id}:{row.table}:{row.source_row_id}",
            "import_batch_id": self.batch_id,
            "source_table": row.table,
            "source_row_id": row.source_row_id,
            "source_row_sha256": row.row_sha,
            "candidate_row_ids": [],
            "related_bill_row_ids": [],
            "match_key": None,
            "match_cardinality": 0,
            "waybill_no": row.get("waybill_no"),
            "charge_code": row.get("charge_code"),
            "billing_period": row.get("billing_period") or None,
            "rule_id": rule["rule_id"] if rule else None,
            "rule_version": self.rule_version,
            "approval_status": rule["approval_status"] if rule else None,
            "formula_id": rule["formula_id"] if rule else None,
            "inputs": {},
            "missing_fields": [],
            "intermediates": {},
            "rounding_mode": rule["rounding_mode"] if rule else None,
            "rounding_scale": rule["rounding_scale"] if rule else None,
            "rounding_layer": rule["rounding_layer"] if rule else None,
            "base_amount_source": None,
            "bill_amount": None,
            "output_amount": None,
            "diff_amount": None,
            "currency": None,
            "tolerance_abs": rule["tolerance_abs"] if rule else None,
            "tolerance_type": rule["tolerance_type"] if rule else None,
            "candidates": [],
            "completeness_flag": completeness_for(codes),
            "exception_candidate_type": exception_type_for(codes),
            "biz_status": status,
            "block_scope": C.SCOPE_NONE if not reasons else C.SCOPE_ROW,
            "block_reason": reasons,
            "human_review_record": None,
            "data_label": C.DATA_LABEL,
        }
        item.update(explanation_placeholder())
        item.update(extra)
        return item

    @staticmethod
    def _calc_view(calc):
        return {
            "source_row_id": calc["source_row_id"],
            "inputs": calc["inputs"],
            "missing_fields": calc["missing_fields"],
            "intermediates": calc["intermediates"],
            "output_amount": dec_str(calc["output_amount"]),
            "base_amount_source": calc["base_amount_source"],
            "calc_block_reason": calc["reasons"],
        }

    def run(self):
        bills, waybills = self.rows[C.TABLE_BILL], self.rows[C.TABLE_WAYBILL]
        items = []
        for table_rows in (bills, waybills):
            seen = {}
            for row in table_rows:
                seen.setdefault(row.source_row_id, []).append(row)
            for dup_rows in seen.values():
                if len(dup_rows) > 1:
                    for row in dup_rows:
                        row.dup_id = True

        bill_issues, bill_amounts, key_of = {}, {}, {}
        self.missing_by_ref = {}
        for row in bills + waybills:
            issues = []
            missing = []
            if getattr(row, "dup_id", False):
                issues.append(_reason("VAL-013", f"{row.table} 内 source_row_id={row.source_row_id} 不唯一"))
            wb_no, cc = row.get("waybill_no"), row.get("charge_code")
            empty = [f for f, v in (("waybill_no", wb_no), ("charge_code", cc)) if not v]
            if empty:
                missing.extend(empty)
                issues.append(_reason("VAL-014", f"匹配键组成字段为空 {empty}；禁止按金额或其他非约定键模糊匹配"))
            elif C.KEY_DELIMITER in wb_no or C.KEY_DELIMITER in cc:
                issues.append(_reason("VAL-014", "字段值含模拟分隔符 |，无法安全构键；保留原值，待人工确认"))
            else:
                key_of[row.ref] = (wb_no, cc)
            if row.table == C.TABLE_BILL:
                period = row.get("billing_period")
                if not period:
                    missing.append("billing_period")
                    issues.append(_reason("VAL-010", "billing_period 为空"))
                elif not _PERIOD_RE.match(period):
                    issues.append(_reason("VAL-010", "billing_period 无法识别（模拟格式 SIM-YYYY-MM）"))
                amount_raw = row.get("bill_amount")
                if not amount_raw:
                    missing.append("bill_amount")
                    issues.append(_reason("VAL-011", "bill_amount 为空"))
                else:
                    amount = parse_decimal(amount_raw, allow_negative=True, max_scale=C.BILL_AMOUNT_MAX_SCALE)
                    if amount is None:
                        issues.append(_reason("VAL-011", "bill_amount 不是合法十进制字符串（至多 2 位小数）"))
                    bill_amounts[row.ref] = amount
                currency = row.get("currency")
                if not currency:
                    missing.append("currency")
                    issues.append(_reason("VAL-012", "currency 为空"))
                elif currency not in C.SIM_CURRENCIES:
                    issues.append(_reason("VAL-012", f"currency={currency} 超出模拟约定（仅 CNY），禁止折算"))
            bill_issues[row.ref] = issues
            self.missing_by_ref[row.ref] = missing

        bill_by_key, wb_by_key = {}, {}
        for row in bills:
            if row.ref in key_of:
                bill_by_key.setdefault(key_of[row.ref], []).append(row)
        for row in waybills:
            if row.ref in key_of:
                wb_by_key.setdefault(key_of[row.ref], []).append(row)

        for row in bills + waybills:
            if row.ref not in key_of:
                items.append(self._item(row, self.rules.get(row.get("charge_code")), bill_issues[row.ref],
                                        missing_fields=self.missing_by_ref[row.ref],
                                        bill_amount=(row.get("bill_amount") or None) if row.table == C.TABLE_BILL else None,
                                        currency=row.get("currency") or None))

        keys = sorted(set(bill_by_key) | set(wb_by_key))

        def is_dependent(key):
            rule = self.rules.get(key[1])
            return rule is not None and base_charge_code(rule)[0] is not None

        for phase_dependent in (False, True):
            for key in keys:
                if is_dependent(key) != phase_dependent:
                    continue
                key_items = self._key_items(key, bill_by_key.get(key, []), wb_by_key.get(key, []),
                                            bill_issues, bill_amounts, wb_by_key, phase_dependent)
                self.status_by_key[key] = [i["biz_status"] for i in key_items]
                items.extend(key_items)

        order = {C.TABLE_BILL: 0, C.TABLE_WAYBILL: 1}
        file_line = {r.ref: r.file_line for r in bills + waybills}
        items.sort(key=lambda i: (order[i["source_table"]], file_line[f"{i['source_table']}.{i['source_row_id']}"]))
        return items

    def _key_items(self, key, key_bills, key_wbs, bill_issues, bill_amounts, wb_by_key, dependent):
        rule = self.rules.get(key[1])
        match_key = f"{key[0]}{C.KEY_DELIMITER}{key[1]}"
        base, base_extra = (None, None)
        if dependent:
            base, base_extra = self.base_for(key[0], rule, wb_by_key)
        cand_ids = [w.ref for w in key_wbs]
        items = []
        if not key_bills:
            for wb in key_wbs:
                calc = self.calc(wb, base)
                reasons = list(bill_issues[wb.ref])
                reasons.append(_reason("REC-013", f"运单 {wb.ref} 在本批账单无对侧行；规则金额仅供复核，不得默认清账"))
                reasons.extend(calc["reasons"])
                if base_extra and calc["output_amount"] is not None:
                    reasons.append(base_extra)
                items.append(self._item(
                    wb, rule, reasons, match_key=match_key, match_cardinality=0,
                    candidate_row_ids=[wb.ref], inputs=calc["inputs"], missing_fields=calc["missing_fields"],
                    intermediates=calc["intermediates"], base_amount_source=calc["base_amount_source"],
                    output_amount=dec_str(calc["output_amount"]),
                    currency=rule["currency"] if rule else None,
                    billing_period=wb.get("billing_period") or None,
                ))
            return items
        bill_ids = [b.ref for b in key_bills]
        for bill in key_bills:
            reasons = list(bill_issues[bill.ref])
            extra = {"match_key": match_key, "match_cardinality": len(key_wbs), "candidate_row_ids": cand_ids,
                     "related_bill_row_ids": [b for b in bill_ids if b != bill.ref],
                     "bill_amount": bill.get("bill_amount") or None, "currency": bill.get("currency") or None}
            if len(key_bills) > 1:
                reasons.append(_reason("REC-011", f"账单侧同键 {len(key_bills)} 行：{bill_ids}；单行可算对也不得判通过"))
            if not key_wbs:
                reasons.append(_reason("REC-012", f"账单 {bill.ref} 在本批运单明细无对侧行；不得用账单额反推"))
            elif len(key_wbs) > 1:
                reasons.append(_reason("REC-010", f"一账单行对应 {len(key_wbs)} 条运单候选 {cand_ids}；禁止自动选定"))
                extra["candidates"] = [self._calc_view(self.calc(w, base)) for w in key_wbs]
            else:
                wb = key_wbs[0]
                calc = self.calc(wb, base)
                reasons.extend(calc["reasons"])
                if base_extra and calc["output_amount"] is not None:
                    reasons.append(base_extra)
                extra.update(inputs=calc["inputs"], missing_fields=calc["missing_fields"],
                             intermediates=calc["intermediates"], base_amount_source=calc["base_amount_source"],
                             output_amount=dec_str(calc["output_amount"]))
                wb_currency = wb.get("currency")
                if wb_currency and bill.get("currency") and wb_currency != bill.get("currency"):
                    reasons.append(_reason("CAL-004", "账单与运单币种不一致，禁止折算"))
                amount = bill_amounts.get(bill.ref)
                if calc["output_amount"] is not None and amount is not None:
                    diff = exact_sub(amount, calc["output_amount"])
                    extra["diff_amount"] = dec_str(diff)
                    tolerance = parse_decimal(str(rule["tolerance_abs"]), allow_negative=False)
                    if abs(diff) > tolerance:
                        reasons.append(_reason("CAL-005", f"差额 {dec_str(diff)}（账单减规则）超出容差 {rule['tolerance_abs']}"))
            extra["missing_fields"] = sorted(set(extra.get("missing_fields", [])) | set(self.missing_by_ref[bill.ref]))
            items.append(self._item(bill, rule, reasons, **extra))
        return items


def _batch_block_items(batch_id, rows, reasons, rule_version):
    items = []
    for table in C.SOURCE_TABLES:
        for row in rows[table]:
            items.append({
                "line_uid": f"{batch_id}:{row.table}:{row.source_row_id}",
                "import_batch_id": batch_id,
                "source_table": row.table,
                "source_row_id": row.source_row_id,
                "source_row_sha256": row.row_sha,
                "candidate_row_ids": [],
                "match_key": None,
                "match_cardinality": 0,
                "waybill_no": row.get("waybill_no") or None,
                "charge_code": row.get("charge_code") or None,
                "billing_period": row.get("billing_period") or None,
                "rule_id": None,
                "rule_version": rule_version,
                "formula_id": None,
                "inputs": {},
                "missing_fields": [],
                "intermediates": {},
                "rounding_mode": None,
                "rounding_scale": None,
                "rounding_layer": None,
                "bill_amount": (row.get("bill_amount") or None) if row.table == C.TABLE_BILL else None,
                "output_amount": None,
                "diff_amount": None,
                "currency": row.get("currency") or None,
                "completeness_flag": C.CF_NOT_EVALUATED,
                "exception_candidate_type": C.EX_OTHER,
                "biz_status": C.ST_PENDING,
                "block_scope": C.SCOPE_BATCH,
                "block_reason": reasons,
                "human_review_record": None,
                "data_label": C.DATA_LABEL,
                **explanation_placeholder(),
            })
    return items


def _summary(items):
    summary = {"total_items": len(items), "by_biz_status": {}, "by_block_scope": {}, "by_exception_candidate_type": {}}
    for item in items:
        for field, bucket in (("biz_status", "by_biz_status"), ("block_scope", "by_block_scope"),
                              ("exception_candidate_type", "by_exception_candidate_type")):
            summary[bucket][item[field]] = summary[bucket].get(item[field], 0) + 1
    return summary


def reconcile(workdir, batch_id, mapping_hash, rule_version, rule_catalog_sha256):
    batch = load_batch(workdir, batch_id)
    batch_dir = os.path.join(workdir, "batches", batch_id)
    try:
        mapping = load_confirmed(workdir, batch, mapping_hash)
    except ReconError as err:
        refusal = {"import_batch_id": batch_id, "code": err.code, "detail": err.detail,
                   "requested_mapping_hash": mapping_hash, "auto_match_executed": False,
                   "calculation_executed": False, "pass_export_possible": False}
        name = sha256_obj(refusal)[:16]
        path = os.path.join(batch_dir, "gate_refusals", f"{name}.json")
        if not os.path.exists(path):
            write_new_file(path, pretty_json(refusal))
        log_event(workdir, "reconcile_refused", import_batch_id=batch_id, code=err.code, exit_code=err.exit_code)
        err.payload = refusal
        raise

    rows, integrity, guard = _load_rows(batch, batch_dir, mapping)
    batch_reasons = []
    for table, info in integrity.items():
        if not (info["file_unchanged"] and info["row_digests_unchanged"]):
            batch_reasons.append(_reason("SRC-001", f"{table} 源副本哈希与导入时不一致"))

    snap = load_snapshot(os.path.join(batch_dir, "inputs"), batch)
    catalog = snap.get("catalog")
    rule_snapshot = {"requested_rule_version": rule_version, "requested_rule_catalog_sha256": rule_catalog_sha256,
                     "catalog_status": snap["status"]}
    if snap["status"] == "LOAD":
        batch_reasons.append(_reason("VAL-002", {"trigger": "LOAD", "detail": snap["detail"],
                                                 "problems": snap.get("problems", [])}))
    elif snap["status"] == "UNFROZEN":
        batch_reasons.append(_reason("VAL-002", {"trigger": "UNFROZEN", "detail": snap["detail"]}))
    else:
        snapshot = snap["snapshot"]
        rule_snapshot.update({k: snapshot.get(k) for k in ("rule_snapshot_id", "rule_version", "rule_catalog_sha256",
                                                            "frozen_at", "approval_status")})
        if rule_version != snapshot["rule_version"] or rule_catalog_sha256 != snapshot["rule_catalog_sha256"]:
            batch_reasons.append(_reason("REC-002", "请求的 rule_version 或 rule_catalog_sha256 与冻结快照不一致"))

    charge_codes = {r.get("charge_code") for t in C.SOURCE_TABLES for r in rows[t]}
    presence = column_presence(mapping["tables"], waybill_required_fields(catalog, charge_codes))
    missing = {t: p["missing_columns"] for t, p in presence.items() if p["missing_columns"]}
    if missing:
        batch_reasons.append(_reason("VAL-001", {"missing_columns": missing,
                                                 "note": "整列不存在；禁止默认值填补、禁止零差异"}))
    else:
        periods = [r.get("billing_period") for r in rows[C.TABLE_BILL]]
        recognized = sorted({p for p in periods if _PERIOD_RE.match(p)})
        if len(recognized) != 1:
            batch_reasons.append(_reason("VAL-003", {
                "recognized_periods": recognized,
                "detail": "本批无可识别账期" if not recognized else "本批出现多个账期，原型不做跨期",
            }))

    run_id = "RR-SIM-" + sha256_obj([batch_id, mapping["mapping_hash"], rule_version, rule_catalog_sha256,
                                    C.ENGINE_VERSION, C.ENG_SIM_VERSION])[:16]
    effective_version = catalog["rule_version"] if catalog else rule_version
    if batch_reasons:
        items = _batch_block_items(batch_id, rows, batch_reasons, effective_version)
        batch_status = C.BATCH_BLOCKED
    else:
        items = Engine(batch_id, catalog, rows).run()
        batch_status = C.BATCH_OK
    derived = {t: _derived_copy(batch_id, t, rows[t], integrity) for t in C.SOURCE_TABLES}
    derived_text = {t: pretty_json(d) for t, d in derived.items()}
    results = {
        "data_label": C.DATA_LABEL,
        "engine_version": C.ENGINE_VERSION,
        "eng_sim_version": C.ENG_SIM_VERSION,
        "api_version": C.API_VERSION,
        "references": {"biz_template": C.BIZ_TEMPLATE_ID, "prd": C.PRD_ID, "eng_design": C.ENG_DESIGN_ID,
                       "eval_baseline": C.EVAL_BASELINE_ID},
        "import_batch_id": batch_id,
        "reconcile_run_id": run_id,
        "input_bundle_sha256": batch["input_bundle_sha256"],
        "rule_snapshot": rule_snapshot,
        "mapping": {k: mapping[k] for k in ("mapping_id", "mapping_hash", "mapping_version", "confirmed_by",
                                            "confirmed_at", "confirmation_label")},
        "batch_status": batch_status,
        "batch_block_reasons": batch_reasons,
        "source_integrity": integrity,
        "derived_copies": {t: {"derived_copy_id": derived[t]["derived_copy_id"],
                               "derived_sha256": sha256_bytes(derived_text[t].encode("utf-8"))}
                           for t in C.SOURCE_TABLES},
        "summary": _summary(items),
        "ai_explanation_status": "skipped",
        "independent_eval_status": C.INDEPENDENT_EVAL_STATUS,
        "business_acceptance_status": C.BUSINESS_ACCEPTANCE_STATUS,
        "side_effects": {"source_files_written": 0, "ledger_posts": 0, "payments": 0, "network_calls": 0},
        "items": items,
    }
    results_text = guard.check(pretty_json(results), "results")
    for text in derived_text.values():
        guard.check(text, "derived_copy")
    results_sha = sha256_bytes(results_text.encode("utf-8"))
    run_dir = os.path.join(batch_dir, "runs", run_id)
    results_path = os.path.join(run_dir, "results.json")
    replayed = os.path.exists(results_path)
    if replayed:
        stored = read_json(os.path.join(run_dir, "run.json"))
        if stored["results_sha256"] != results_sha:
            raise ReconError("SYS-001", "重跑结果与既有跑次不一致（非确定性），拒绝覆盖", C.EXIT_INTERNAL)
    else:
        write_new_file(results_path, results_text)
        for table, text in derived_text.items():
            write_new_file(os.path.join(run_dir, "derived", f"{derived[table]['derived_copy_id']}.json"), text)
        write_new_file(os.path.join(run_dir, "run.json"), pretty_json({
            "reconcile_run_id": run_id, "import_batch_id": batch_id, "results_sha256": results_sha,
            "batch_status": batch_status, "mapping_hash": mapping["mapping_hash"],
            "rule_version": rule_version, "rule_catalog_sha256": rule_catalog_sha256,
        }))
    log_event(workdir, "reconciled", guard=guard, import_batch_id=batch_id, reconcile_run_id=run_id,
              results_sha256=results_sha, batch_status=batch_status, replayed=replayed,
              codes=sorted({r["code"] for r in batch_reasons}), row_count=len(items))
    payload = {
        "import_batch_id": batch_id,
        "reconcile_run_id": run_id,
        "batch_status": batch_status,
        "batch_block_codes": [r["code"] for r in batch_reasons],
        "summary": results["summary"],
        "results_path": os.path.relpath(results_path, workdir),
        "results_sha256": results_sha,
        "replayed": replayed,
        "independent_eval_status": C.INDEPENDENT_EVAL_STATUS,
    }
    guard.check(pretty_json(payload), "stdout")
    return payload, (C.EXIT_BATCH_BLOCKED if batch_reasons else C.EXIT_OK)
