"""Whitelisted JSON-lines log. Never records row payloads or sensitive text."""

import os

from .util import canonical_json, now_shanghai

LOG_FIELD_WHITELIST = (
    "event", "import_batch_id", "rejection_id", "source_table", "source_row_id", "code",
    "codes", "exit_code", "ingest_status", "input_bundle_sha256", "rule_catalog_sha256",
    "rule_snapshot_id", "rule_version", "mapping_id", "mapping_hash", "reconcile_run_id",
    "results_sha256", "export_id", "evidence_pack_sha256", "replayed", "batch_status",
    "row_count", "action", "decision", "idempotency_key",
)


def log_event(workdir, event, guard=None, **fields):
    record = {"ts": now_shanghai(), "event": event}
    for key in LOG_FIELD_WHITELIST:
        if key in fields and fields[key] is not None:
            record[key] = fields[key]
    line = canonical_json(record)
    if guard is not None:
        guard.check(line, "log")
    path = os.path.join(workdir, "logs", "recon.log.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
