"""Run all R&D self-tests and write a per-case matrix.

The matrix records developer self-test outcomes only. The independent evaluation of
EVAL-TD-BASELINE-2026-11-12-R1 is recorded as "未执行" regardless of these results.
Usage: python3 tests/run_selftest.py [--out artifacts/selftest]
"""

import argparse
import json
import os
import platform
import re
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

from frecon import conventions as C  # noqa: E402

DELIVERY_REVISION = "frecon 0.1.0 / delivery r2（PRD-CHG-2026-11-12-A 对齐；引擎逻辑未变，仅测试分类、补测与文档）"

BASELINE_CASES = {
    "TC-01": "正常一对一金额一致", "TC-02": "账单侧重复计费", "TC-03": "账单有、运单明细缺失",
    "TC-04": "运单明细有、账单缺失", "TC-05": "金额不一致", "TC-06": "规则缺失", "TC-07": "缺计费重量字段",
    "TC-08": "匹配不唯一", "TC-09": "舍入边界一致 1.005→1.01", "TC-10": "舍入边界不一致（截断 1.00）",
    "TC-11": "证据链完整性", "TC-12": "敏感数据输出抑制", "TC-13": "缺匹配键（运单号为空）",
    "TC-14": "多步计算与舍入层级", "TC-15": "同运单两项收费均一致", "TC-16": "字段映射必须人工确认",
    "TC-17": "源数据不可改写", "TC-18": "禁止记账", "TC-19": "禁止付款", "TC-20": "批级整列缺失阻断",
    "TC-21": "FS001 依赖 FR001 阻断", "TC-22": "分隔符碰撞", "TC-23": "未登记公式/自由表达式拒绝求值",
    "TC-24": "CSV 公式注入拒绝（负金额对照）", "TC-25": "幂等重跑", "TC-26": "多错误优先级归并",
    "TC-27": "批级：编码失败与格式失败", "TC-28": "批级：目录无法加载/未冻结/快照不一致",
    "TC-29": "批级：批次账期无法识别",
}
SELF_BUILT = {
    "TC-D01": ("分隔符碰撞（交换位置）", "TC-22"), "TC-D02": ("恶意/可求值形态公式与非模拟目录", "TC-23"),
    "TC-D03": ("输出 CSV 公式注入防护", "TC-24"), "TC-D04": ("重跑一致与篡改拒绝", "TC-25"),
    "TC-D05": ("账单侧整列缺失 / 单格为空区分", "TC-20"), "TC-D06": ("多条件优先级组合", "TC-26"),
    "TC-D07": ("附加费依赖：基础不唯一/缺失/未入账", "TC-21"), "TC-D08": ("映射未确认与非法确认", "TC-16"),
}


SUPPLEMENTS = {
    "TC-16-S1": ("补 currency 仅消除缺列障碍（DEC-SIM-C3-TC16）", "TC-16"),
    "TC-20-S1": ("IMP-004 仅用于未登记表头（DEC-SIM-C1-VAL001）", "TC-20"),
    "TC-21-S1": ("基础行失败不被覆盖、附加费不得通过（DEC-SIM-C6-CAL006）", "TC-21"),
}


def case_of(test_id):
    name = test_id.rsplit(".", 1)[-1]
    m = re.match(r"test_TC_(\d\d)_S(\d)", name)
    if m:
        return f"TC-{m.group(1)}-S{m.group(2)}"
    m = re.match(r"test_TC_D(\d\d)", name)
    if m:
        return f"TC-D{m.group(1)}"
    m = re.match(r"test_TC_(\d\d)", name)
    if m:
        return f"TC-{m.group(1)}"
    return "UNIT" if name.startswith("test_UNIT") else "SUPPORT"


class Recorder(unittest.TextTestResult):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.records = []

    def _rec(self, test, outcome, detail=""):
        self.records.append({"test_id": test.id(), "case": case_of(test.id()), "outcome": outcome,
                             "detail": detail[-2000:]})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._rec(test, "pass")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._rec(test, "fail", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._rec(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._rec(test, "skipped", reason)


def summarise(records, cases, kind):
    rows = []
    for case_id, meta in cases.items():
        recs = [r for r in records if r["case"] == case_id]
        passed = sum(r["outcome"] == "pass" for r in recs)
        if not recs:
            selftest = "无自测（未覆盖）"
        elif passed == len(recs):
            selftest = "研发自测通过"
        else:
            selftest = "研发自测未通过"
        row = {"case": case_id, "scene": meta if isinstance(meta, str) else meta[0], "kind": kind,
               "tests": len(recs), "passed": passed, "failed": len(recs) - passed, "selftest_result": selftest,
               "independent_eval_status": {"baseline": "未执行",
                                           "supplement": "未纳入（补充用例，是否纳入评测版本由评测负责人登记）"}.get(
                   kind, "不适用（自建补测，非基线用例）")}
        if kind in ("self-built", "supplement"):
            row["related_baseline_case"] = meta[1]
        rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=os.path.join(ROOT, "artifacts", "selftest"))
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)
    runner = unittest.TextTestRunner(resultclass=Recorder, verbosity=2, stream=sys.stdout)
    started = time.time()
    result = runner.run(suite)
    elapsed = round(time.time() - started, 3)
    records = result.records
    baseline = summarise(records, BASELINE_CASES, "baseline")
    built = summarise(records, SELF_BUILT, "self-built")
    supp = summarise(records, SUPPLEMENTS, "supplement")
    totals = {o: sum(r["outcome"] == o for r in records) for o in ("pass", "fail", "error", "skipped")}
    report = {
        "title": "研发自测报告（非独立评测、非业务验收）",
        "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "delivery_revision": DELIVERY_REVISION, "engine_version": C.ENGINE_VERSION, "eng_sim_version": C.ENG_SIM_VERSION,
        "sim_rule_version": C.SIM_RULE_VERSION, "eval_baseline": C.EVAL_BASELINE_ID,
        "python": platform.python_version(), "platform": platform.platform(),
        "git_commit_under_test": os.environ.get("FRECON_GIT_COMMIT", "unknown"),
        "tests_run": result.testsRun, "totals": totals, "elapsed_seconds": elapsed,
        "independent_eval_status": C.INDEPENDENT_EVAL_STATUS,
        "business_acceptance_status": C.BUSINESS_ACCEPTANCE_STATUS,
        "baseline_cases": baseline, "self_built_cases": built, "supplement_cases": supp,
        "tests": [{k: v for k, v in r.items() if k != "detail" or v} for r in records],
    }
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "selftest_report.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = [
        "# 研发自测用例矩阵（非独立评测、非业务验收）", "",
        f"- 基线：{C.EVAL_BASELINE_ID}（29 条，独立评测状态：**未执行**）",
        f"- 交付修订：{DELIVERY_REVISION}",
        f"- 规则：{C.SIM_RULE_VERSION}；引擎 {C.ENGINE_VERSION}；工程模拟约定 {C.ENG_SIM_VERSION}",
        f"- 被测提交：{report['git_commit_under_test']}；Python {report['python']}；生成时间 {report['generated_at']}",
        f"- 测试数 {result.testsRun}：通过 {totals['pass']}，失败 {totals['fail']}，错误 {totals['error']}，"
        f"跳过 {totals['skipped']}；耗时 {elapsed}s", "",
        "## 评测基线 TC-01–TC-29（研发自测结果 ≠ 独立评测结论）", "",
        "| 用例 | 场景 | 自测数 | 通过 | 研发自测 | 独立评测 |", "|---|---|---|---|---|---|",
    ]
    lines += [f"| {r['case']} | {r['scene']} | {r['tests']} | {r['passed']} | {r['selftest_result']} | "
              f"{r['independent_eval_status']} |" for r in baseline]
    lines += ["", "## 自建补测 TC-D01–TC-D08（R2 设计用例，非基线、不计入独立评测）", "",
              "| 用例 | 场景 | 关联基线 | 自测数 | 通过 | 研发自测 |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['scene']} | {r['related_baseline_case']} | {r['tests']} | {r['passed']} | "
              f"{r['selftest_result']} |" for r in built]
    lines += ["", "## 补充用例（按 PRD-CHG-2026-11-12-A 拆分单列；非冻结基线、非独立评测）", "",
              "| 用例 | 场景 | 关联基线 | 自测数 | 通过 | 研发自测 | 评测纳入 |", "|---|---|---|---|---|---|---|"]
    lines += [f"| {r['case']} | {r['scene']} | {r['related_baseline_case']} | {r['tests']} | {r['passed']} | "
              f"{r['selftest_result']} | {r['independent_eval_status']} |" for r in supp]
    other = [r for r in records if r["case"] in ("UNIT", "SUPPORT")]
    lines += ["", f"另含单元/支撑测试 {len(other)} 个，通过 {sum(r['outcome'] == 'pass' for r in other)} 个。", ""]
    with open(os.path.join(args.out, "selftest_matrix.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
