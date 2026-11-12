"""M-EVD + M-SEC: whitelisted, redacted, formula-safe evidence export."""

import csv
import io
import json
import os

from . import conventions as C
from .audit_log import log_event
from .ingest import formula_prefix, load_batch
from .mapping import load_confirmed
from .reconcile import _load_rows
from .util import ReconError, pretty_json, read_json, sha256_bytes, sha256_obj, write_new_file

EXPORT_FIELDS = (
    "line_uid", "import_batch_id", "source_table", "source_row_id", "source_row_sha256",
    "candidate_row_ids", "related_bill_row_ids", "match_key", "match_cardinality", "waybill_no",
    "charge_code", "billing_period", "rule_id", "rule_version", "approval_status", "formula_id",
    "inputs", "missing_fields", "intermediates", "rounding_mode", "rounding_scale", "rounding_layer",
    "base_amount_source", "bill_amount", "output_amount", "diff_amount", "currency", "tolerance_abs",
    "tolerance_type", "candidates", "completeness_flag", "exception_candidate_type", "biz_status",
    "block_scope", "block_reason", "human_review_record", "ai_explanation", "ai_explanation_status",
    "data_label",
)
HEADER_FIELDS = (
    "data_label", "engine_version", "eng_sim_version", "references", "import_batch_id",
    "reconcile_run_id", "input_bundle_sha256", "rule_snapshot", "mapping", "batch_status",
    "batch_block_reasons", "source_integrity", "derived_copies", "summary", "ai_explanation_status",
    "independent_eval_status", "business_acceptance_status", "side_effects",
)


def sanitize_cell(text):
    """Neutralise spreadsheet formula prefixes in exported CSV cells (plain negative decimals stay)."""
    if text is None:
        return ""
    if formula_prefix(text) is not None:
        return "'" + text
    return text


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def render_csv(items):
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    writer.writerow(EXPORT_FIELDS)
    for item in items:
        writer.writerow([sanitize_cell(_cell(item.get(f))) for f in EXPORT_FIELDS])
    return buf.getvalue()


def export(workdir, batch_id, run_id, fmt):
    if fmt not in ("json", "csv"):
        raise ReconError("SYS-001", "format 仅支持 json / csv", C.EXIT_USAGE)
    batch = load_batch(workdir, batch_id)
    batch_dir = os.path.join(workdir, "batches", batch_id)
    run_dir = os.path.join(batch_dir, "runs", run_id or "-")
    if not run_id or not os.path.exists(os.path.join(run_dir, "run.json")):
        raise ReconError("EXP-001", "核对跑次不存在（映射未确认或导入被拒时不产生可导出跑次）", C.EXIT_REFUSED)
    run = read_json(os.path.join(run_dir, "run.json"))
    with open(os.path.join(run_dir, "results.json"), "rb") as fh:
        results_bytes = fh.read()
    if sha256_bytes(results_bytes) != run["results_sha256"]:
        raise ReconError("SYS-001", "results.json 与跑次记录哈希不一致，拒绝导出", C.EXIT_INTERNAL)
    results = json.loads(results_bytes.decode("utf-8"))
    mapping = load_confirmed(workdir, batch, run["mapping_hash"])
    _rows, _integrity, guard = _load_rows(batch, batch_dir, mapping)

    items = [{f: item.get(f) for f in EXPORT_FIELDS} for item in results["items"]]
    header = {f: results.get(f) for f in HEADER_FIELDS}
    header.update({"redaction_profile": "shareable", "redaction_applied": True,
                   "pass_candidate_count": sum(1 for i in items if i["biz_status"] == C.ST_PASS)})
    if fmt == "json":
        text = pretty_json({**header, "items": items})
    else:
        text = render_csv(items)
    guard.check(text, f"export.{fmt}")
    pack_sha = sha256_bytes(text.encode("utf-8"))
    export_id = "EX-SIM-" + sha256_obj([run_id, fmt])[:16]
    export_dir = os.path.join(batch_dir, "exports", export_id)
    path = os.path.join(export_dir, f"evidence.{fmt}")
    manifest = {
        "export_id": export_id, "import_batch_id": batch_id, "reconcile_run_id": run_id, "format": fmt,
        "evidence_pack_sha256": pack_sha, "redaction_profile": "shareable", "redaction_applied": True,
        "batch_status": results["batch_status"], "item_count": len(items),
        "pass_candidate_count": header["pass_candidate_count"], "data_label": C.DATA_LABEL,
        "independent_eval_status": C.INDEPENDENT_EVAL_STATUS,
    }
    replayed = os.path.exists(path)
    if replayed:
        if read_json(os.path.join(export_dir, "manifest.json"))["evidence_pack_sha256"] != pack_sha:
            raise ReconError("SYS-001", "同参重出导出包哈希不一致，拒绝覆盖", C.EXIT_INTERNAL)
    else:
        write_new_file(path, text)
        write_new_file(os.path.join(export_dir, "manifest.json"), pretty_json(manifest))
    log_event(workdir, "exported", guard=guard, import_batch_id=batch_id, reconcile_run_id=run_id,
              export_id=export_id, evidence_pack_sha256=pack_sha, replayed=replayed)
    return {**manifest, "path": os.path.relpath(path, workdir), "replayed": replayed}, C.EXIT_OK
