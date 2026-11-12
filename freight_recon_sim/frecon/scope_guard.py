"""FR-RO: static deny-all permissions and isolated mocks for out-of-scope actions.

The prototype has no ledger, payment or source-write execution path. Any such request is
refused and recorded in an isolated local mock log; nothing is sent anywhere.
"""

import json
import os

from . import conventions as C
from .audit_log import log_event
from .util import canonical_json, mask_value, now_shanghai

PERMISSIONS = {
    "SIM_PERM_LEDGER": {"allowed": [], "denied": ["ledger:post", "voucher:create", "account:write"]},
    "SIM_PERM_PAY": {"allowed": [], "denied": ["payment:create", "bank:transfer", "payout:execute"]},
    "SIM_PERM_SOURCE": {"allowed": ["source:read"], "denied": ["source:write", "source:delete"]},
}
MOCKS = {
    "SIM_PERM_LEDGER": "SIM-MOCK-LEDGER",
    "SIM_PERM_PAY": "SIM-MOCK-PAY",
    "SIM_PERM_SOURCE": "SIM-MOCK-SOURCE-WRITE",
}


def permission_snapshot():
    return {
        "permissions": PERMISSIONS,
        "note": "静态权限快照：记账、付款、改写源数据全部拒绝；无对应执行路径；无外部连接。",
        "data_label": C.DATA_LABEL,
    }


def _domain(action):
    prefix = action.split(":", 1)[0]
    if prefix in ("ledger", "voucher", "account"):
        return "SIM_PERM_LEDGER"
    if prefix in ("payment", "bank", "payout", "pay"):
        return "SIM_PERM_PAY"
    if prefix == "source":
        return "SIM_PERM_SOURCE"
    return None


def request_action(workdir, action, note=None):
    domain = _domain(action or "")
    mock = MOCKS.get(domain, "SIM-MOCK-OUT-OF-SCOPE")
    record = {
        "ts": now_shanghai(),
        "mock": mock,
        "action": action,
        "decision": "rejected",
        "code": "OOS-001",
        "block_reason": "超出对账范围",
        "committed": 0,
        "posted": 0,
        "paid": 0,
        "transferred": 0,
        "written": 0,
        "request_note_masked": mask_value(note) if note else None,
        "isolation": "本地隔离 mock，未连接财务、银行或任何外部系统",
    }
    path = os.path.join(workdir, "mock", f"{mock}.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(canonical_json(record) + "\n")
    log_event(workdir, "out_of_scope_refused", action=action, decision="rejected", code="OOS-001",
              exit_code=C.EXIT_REFUSED)
    return record


def mock_counters(workdir):
    totals = {}
    mock_dir = os.path.join(workdir, "mock")
    if not os.path.isdir(mock_dir):
        return totals
    for name in sorted(os.listdir(mock_dir)):
        with open(os.path.join(mock_dir, name), "r", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh if line.strip()]
        totals[name[:-6]] = {
            "attempts": len(records),
            "rejected": sum(1 for r in records if r["decision"] == "rejected"),
            "successful": sum(r["posted"] + r["paid"] + r["transferred"] + r["written"] + r["committed"]
                              for r in records),
        }
    return totals
