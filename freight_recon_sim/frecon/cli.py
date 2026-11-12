"""Offline CLI. Every command prints one JSON document on stdout.

Exit codes: 0 ok, 2 batch blocked (results written, no pass), 3 refused, 4 data-protection
refusal (SEC-001), 64 usage error, 70 internal error.
"""

import argparse
import json
import sys

from . import conventions as C
from .evidence import export
from .ingest import import_batch, load_batch
from .mapping import confirm, validate
from .reconcile import reconcile
from .rules import load_snapshot, waybill_required_fields
from .scope_guard import mock_counters, permission_snapshot, request_action
from .util import ReconError, read_json


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"usage error: {message}\n")
        sys.exit(C.EXIT_USAGE)


def _bool(text):
    if text not in ("true", "false"):
        raise argparse.ArgumentTypeError("must be true or false")
    return text == "true"


def build_parser():
    p = _Parser(prog="python3 -m frecon", description="运费账单对账离线原型（模拟）")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("import", help="导入脱敏 CSV，冻结哈希与规则快照")
    s.add_argument("--workdir", required=True)
    s.add_argument("--bill", required=True)
    s.add_argument("--waybill", required=True)
    s.add_argument("--rules", help="规则目录 JSON；缺省使用内置冻结模拟目录 " + C.SIM_RULE_VERSION)
    s.add_argument("--snapshot", help="冻结快照 JSON；与 --rules 同时给出")
    s.add_argument("--idempotency-key", required=True)
    s.add_argument("--declared-encoding", required=True)
    s.add_argument("--declared-delimiter", required=True, choices=sorted(C.ALLOWED_DELIMITERS))
    s.add_argument("--source-declared-desensitized", required=True, type=_bool)

    s = sub.add_parser("validate", help="生成映射建议（proposed），不确认")
    s.add_argument("--workdir", required=True)
    s.add_argument("--batch", required=True)
    s.add_argument("--ai-proposal", help="离线提供的 AI 候选映射 JSON（仅作建议）")

    s = sub.add_parser("confirm-mapping", help="人工确认字段映射")
    s.add_argument("--workdir", required=True)
    s.add_argument("--batch", required=True)
    s.add_argument("--mapping", required=True)
    s.add_argument("--confirmed-by", required=True)
    s.add_argument("--confirmed-at", required=True, help="YYYY-MM-DDTHH:MM:SS+08:00")

    s = sub.add_parser("reconcile", help="按冻结规则确定性核对")
    s.add_argument("--workdir", required=True)
    s.add_argument("--batch", required=True)
    s.add_argument("--mapping-hash", default="")
    s.add_argument("--rule-version", required=True)
    s.add_argument("--rule-catalog-sha256", required=True)

    s = sub.add_parser("export", help="导出脱敏证据包")
    s.add_argument("--workdir", required=True)
    s.add_argument("--batch", required=True)
    s.add_argument("--run", required=True)
    s.add_argument("--format", required=True, choices=["json", "csv"])

    s = sub.add_parser("request-action", help="超范围请求（记账/付款/改写源数据）一律拒绝并记入隔离 mock")
    s.add_argument("--workdir", required=True)
    s.add_argument("--action", required=True)
    s.add_argument("--note")

    s = sub.add_parser("permissions", help="打印静态权限快照与 mock 计数")
    s.add_argument("--workdir")

    sub.add_parser("info", help="版本与引用")
    return p


def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def _run(args):
    if args.command == "import":
        return import_batch(args.workdir, args.bill, args.waybill, args.idempotency_key, args.declared_encoding,
                            args.declared_delimiter, args.source_declared_desensitized,
                            rules_path=args.rules, snapshot_path=args.snapshot)
    if args.command == "validate":
        batch = load_batch(args.workdir, args.batch)
        snap = load_snapshot(f"{args.workdir}/batches/{args.batch}/inputs", batch)
        ai = read_json(args.ai_proposal) if args.ai_proposal else None
        return validate(args.workdir, batch, waybill_required_fields(snap.get("catalog")), ai), C.EXIT_OK
    if args.command == "confirm-mapping":
        batch = load_batch(args.workdir, args.batch)
        return confirm(args.workdir, batch, read_json(args.mapping), args.confirmed_by, args.confirmed_at), C.EXIT_OK
    if args.command == "reconcile":
        return reconcile(args.workdir, args.batch, args.mapping_hash, args.rule_version, args.rule_catalog_sha256)
    if args.command == "export":
        return export(args.workdir, args.batch, args.run, args.format)
    if args.command == "request-action":
        return request_action(args.workdir, args.action, args.note), C.EXIT_REFUSED
    if args.command == "permissions":
        snap = permission_snapshot()
        if args.workdir:
            snap["mock_counters"] = mock_counters(args.workdir)
        return snap, C.EXIT_OK
    return {
        "engine_version": C.ENGINE_VERSION, "eng_sim_version": C.ENG_SIM_VERSION, "api_version": C.API_VERSION,
        "sim_rule_version": C.SIM_RULE_VERSION, "eval_baseline": C.EVAL_BASELINE_ID,
        "biz_template": C.BIZ_TEMPLATE_ID, "prd": C.PRD_ID, "eng_design": C.ENG_DESIGN_ID,
        "data_label": C.DATA_LABEL, "independent_eval_status": C.INDEPENDENT_EVAL_STATUS,
    }, C.EXIT_OK


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        payload, code = _run(args)
    except ReconError as err:
        _emit({"status": "refused", "code": err.code, "meaning": C.CODES.get(err.code), "detail": err.detail,
               **({"gate_record": err.payload} if err.payload else {})})
        return err.exit_code
    except Exception as exc:  # noqa: BLE001 - never echo raw payloads
        _emit({"status": "error", "code": "SYS-001", "meaning": C.CODES["SYS-001"], "detail": type(exc).__name__})
        return C.EXIT_INTERNAL
    _emit(payload)
    return code
