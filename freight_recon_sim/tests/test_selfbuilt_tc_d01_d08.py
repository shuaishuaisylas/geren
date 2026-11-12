"""Self-built supplementary tests TC-D01..TC-D08 (R2 design cases).

These are R&D-authored extra checks beyond the frozen evaluation baseline. They are not part of
EVAL-TD-BASELINE-2026-11-12-R1 and do not count as independent evaluation.
"""

import builtins
import csv
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from helpers import C, ROOT, Pipeline, codes, fx, item
from frecon.evidence import render_csv, sanitize_cell
from frecon.ingest import formula_prefix
from frecon.util import sha256_file

BILL_H = ["source_row_id", "waybill_no", "charge_code", "bill_amount", "billing_period", "currency"]
WB_H = ["source_row_id", "waybill_no", "charge_code", "unit_price", "chargeable_weight", "surcharge_rate"]
P = "SIM-2026-10"


def make_batch(tc, bills, wbs, bill_h=BILL_H, wb_h=WB_H):
    d = tempfile.mkdtemp(prefix="frecon-adhoc-")
    tc.addCleanup(shutil.rmtree, d, True)
    for name, header, rows in (("SIM_BILL.csv", bill_h, bills), ("SIM_WAYBILL.csv", wb_h, wbs)):
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows([header] + rows)
        with open(os.path.join(d, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(buf.getvalue())
    mapping = {"mapping_version": "MAP-SIM-SELFTEST-01",
               "tables": {"SIM_BILL": {h: h for h in bill_h}, "SIM_WAYBILL": {h: h for h in wb_h}}}
    with open(os.path.join(d, "mapping.json"), "w", encoding="utf-8") as fh:
        json.dump(mapping, fh)
    return d


def run_adhoc(tc, bills, wbs, inproc=False, **kw):
    d = make_batch(tc, bills, wbs, **{k: v for k, v in kw.items() if k in ("bill_h", "wb_h")})
    pipe = Pipeline(tc, inproc=inproc)
    code, imp = pipe.import_(os.path.join(d, "SIM_BILL.csv"), os.path.join(d, "SIM_WAYBILL.csv"), "IDEMP-ADHOC",
                             rules=kw.get("rules"), snapshot=kw.get("snapshot"))
    tc.assertEqual(code, 0, imp)
    code, conf = pipe.confirm(imp["import_batch_id"], os.path.join(d, "mapping.json"))
    tc.assertEqual(code, 0, conf)
    rc, rec = pipe.reconcile(imp["import_batch_id"], conf["mapping_hash"], **kw.get("rec_kw", {}))
    return pipe, imp, conf, rc, rec


class TC_D01_DelimiterCollision(unittest.TestCase):
    def test_TC_D01_swapped_delimiter_positions_never_collide(self):
        pipe, _i, _c, rc, rec = run_adhoc(self, [
            ["B1", "WB-A|FR001", "X", "10.00", P, "CNY"],
            ["B2", "WB-A", "FR001|X", "10.00", P, "CNY"],
            ["B3", "WB-A", "FR001", "10.00", P, "CNY"],
        ], [
            ["W1", "WB-A|FR001", "X", "1.00", "10.000", ""],
            ["W2", "WB-A", "FR001|X", "1.00", "10.000", ""],
            ["W3", "WB-A", "FR001", "1.00", "10.000", ""],
        ])
        res = pipe.results(rec)
        for rid in ("B1", "B2", "W1", "W2"):
            it = item(res, rid)
            self.assertEqual((it["biz_status"], codes(it), it["match_key"]), (C.ST_PENDING, {"VAL-014"}, None))
        b3 = item(res, "B3")
        self.assertEqual((b3["biz_status"], b3["candidate_row_ids"]), (C.ST_PASS, ["SIM_WAYBILL.W3"]))


class TC_D02_MaliciousFormula(unittest.TestCase):
    def _catalog(self, mutate, approval=None):
        with open(os.path.join(ROOT, "frecon", "data", "RV-SIM-2026-11-12-A.catalog.json"), encoding="utf-8") as fh:
            cat = json.load(fh)
        cat["rule_version"] = "RV-SIM-SELFTEST-D02"
        for rule in cat["rules"]:
            rule["rule_version"] = "RV-SIM-SELFTEST-D02"
            if approval:
                rule["approval_status"] = approval
        cat["rules"][0].update(mutate)
        d = tempfile.mkdtemp(prefix="frecon-cat-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "cat.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cat, fh, ensure_ascii=False)
        snap = {"rule_snapshot_id": "SNAP-SELFTEST-D02", "rule_version": "RV-SIM-SELFTEST-D02",
                "rule_catalog_sha256": sha256_file(path), "frozen_at": "2026-11-12T10:00:00+08:00"}
        spath = os.path.join(d, "snap.json")
        with open(spath, "w", encoding="utf-8") as fh:
            json.dump(snap, fh)
        return path, spath

    def test_TC_D02_evaluable_looking_expression_is_never_evaluated(self):
        cat, snap = self._catalog({"formula_expr": "P*W+0"})
        calls = []
        real_eval = builtins.eval
        with mock.patch("builtins.eval", lambda *a, **k: calls.append(a) or real_eval(*a, **k)):
            pipe, _i, _c, rc, rec = run_adhoc(
                self, [["B1", "WB-D2", "FR001", "50.00", P, "CNY"]], [["W1", "WB-D2", "FR001", "5.00", "10.000", ""]],
                inproc=True, rules=cat, snapshot=snap,
                rec_kw={"version": "RV-SIM-SELFTEST-D02", "sha": sha256_file(cat)})
        self.assertEqual(calls, [])
        it = item(pipe.results(rec), "B1")
        self.assertEqual((it["biz_status"], codes(it), it["output_amount"]), (C.ST_PENDING, {"RUL-002"}, None))

    def test_TC_D02_non_simulated_approval_status_not_loaded(self):
        cat, snap = self._catalog({}, approval="已启用")
        pipe, _i, _c, rc, rec = run_adhoc(
            self, [["B1", "WB-D2", "FR001", "50.00", P, "CNY"]], [["W1", "WB-D2", "FR001", "5.00", "10.000", ""]],
            rules=cat, snapshot=snap, rec_kw={"version": "RV-SIM-SELFTEST-D02", "sha": sha256_file(cat)})
        self.assertEqual(rc, C.EXIT_BATCH_BLOCKED)
        reason = pipe.results(rec)["batch_block_reasons"][0]
        self.assertEqual((reason["code"], reason["detail"]["trigger"]), ("VAL-002", "LOAD"))


class TC_D03_OutputInjection(unittest.TestCase):
    def test_TC_D03_sanitize_cell(self):
        for raw in ("=1+1", "+1", "@SUM(A1)", "-cmd", "\t=1", " =1"):
            self.assertTrue(sanitize_cell(raw).startswith("'"), raw)
        for raw in ("-0.01", "125.50", "WB-SIM-0001", "", "{\"a\":1}"):
            self.assertEqual(sanitize_cell(raw), raw)

    def test_TC_D03_rendered_csv_neutralises_formula_text(self):
        text = render_csv([{"source_row_id": "=HYPERLINK(\"x\")", "diff_amount": "-0.01", "waybill_no": "@x"}])
        rows = list(csv.reader(io.StringIO(text)))
        cells = dict(zip(rows[0], rows[1]))
        self.assertEqual(cells["source_row_id"][0], "'")
        self.assertEqual(cells["waybill_no"], "'@x")
        self.assertEqual(cells["diff_amount"], "-0.01")

    def test_TC_D03_main_batch_csv_export_has_no_live_formula_cells(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("main")
        code, exp = pipe.export(run["batch"], run["rec"]["reconcile_run_id"], "csv")
        with open(os.path.join(pipe.workdir, exp["path"]), encoding="utf-8") as fh:
            for row in csv.reader(fh):
                for cell in row:
                    self.assertIsNone(formula_prefix(cell), cell)


class TC_D04_Rerun(unittest.TestCase):
    def test_TC_D04_reconcile_and_export_reruns_are_identical_and_not_rewritten(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("main")
        batch, run_id = run["batch"], run["rec"]["reconcile_run_id"]
        path = os.path.join(pipe.workdir, run["rec"]["results_path"])
        mtime = os.stat(path).st_mtime_ns
        _c, exp1 = pipe.export(batch, run_id, "json")
        _c, rec2 = pipe.reconcile(batch, run["mapping"]["mapping_hash"])
        _c, exp2 = pipe.export(batch, run_id, "json")
        self.assertEqual((rec2["results_sha256"], rec2["replayed"]), (run["rec"]["results_sha256"], True))
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)
        self.assertEqual((exp2["evidence_pack_sha256"], exp2["replayed"]), (exp1["evidence_pack_sha256"], True))
        other = Pipeline(self).run_fixture("main")
        self.assertEqual(other["rec"]["results_sha256"], run["rec"]["results_sha256"])

    def test_TC_D04_tampered_results_refused_on_export(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("main")
        path = os.path.join(pipe.workdir, run["rec"]["results_path"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(" ")
        code, out = pipe.export(run["batch"], run["rec"]["reconcile_run_id"], "json")
        self.assertEqual((code, out["code"]), (C.EXIT_INTERNAL, "SYS-001"))


class TC_D05_WholeColumnMissing(unittest.TestCase):
    def test_TC_D05_bill_side_required_columns_missing(self):
        bill_h = ["source_row_id", "waybill_no", "charge_code", "bill_amount"]
        pipe, _i, _c, rc, rec = run_adhoc(self, [["B1", "WB-D5", "FR001", "50.00"]],
                                          [["W1", "WB-D5", "FR001", "5.00", "10.000", ""]], bill_h=bill_h)
        self.assertEqual(rc, C.EXIT_BATCH_BLOCKED)
        res = pipe.results(rec)
        self.assertEqual(res["batch_block_reasons"][0]["detail"]["missing_columns"],
                         {"SIM_BILL": ["billing_period", "currency"]})
        self.assertTrue(all(i["block_scope"] == C.SCOPE_BATCH and i["output_amount"] is None for i in res["items"]))

    def test_TC_D05_single_empty_cell_stays_row_level(self):
        pipe, _i, _c, rc, rec = run_adhoc(self, [["B1", "WB-D5", "FR001", "50.00", P, ""],
                                                 ["B2", "WB-D6", "FR001", "50.00", P, "CNY"]],
                                          [["W1", "WB-D5", "FR001", "5.00", "10.000", ""],
                                           ["W2", "WB-D6", "FR001", "5.00", "10.000", ""]])
        self.assertEqual(rc, C.EXIT_OK)
        res = pipe.results(rec)
        self.assertEqual((item(res, "B1")["block_scope"], codes(item(res, "B1"))), (C.SCOPE_ROW, {"VAL-012"}))
        self.assertEqual(item(res, "B2")["biz_status"], C.ST_PASS)


class TC_D06_Priority(unittest.TestCase):
    def test_TC_D06_combinations_keep_all_reasons(self):
        pipe, _i, _c, rc, rec = run_adhoc(self, [
            ["A1", "WB-61", "XX999", "40.00", P, "CNY"],
            ["B1", "WB-62", "FR001", "99.00", P, "CNY"],
            ["B2", "WB-62", "FR001", "50.00", P, "CNY"],
            ["C1", "WB-63", "FR001", "99.00", P, ""],
            ["D1", "WB-64", "FR001", "10.00", "BAD", "CNY"],
        ], [
            ["WA1", "WB-61", "XX999", "5.00", "8.000", ""],
            ["WA2", "WB-61", "XX999", "5.00", "9.000", ""],
            ["WB1", "WB-62", "FR001", "5.00", "10.000", ""],
            ["WC1", "WB-63", "FR001", "5.00", "10.000", ""],
        ])
        res = pipe.results(rec)
        a1 = item(res, "A1")
        self.assertEqual((a1["biz_status"], codes(a1)), (C.ST_PENDING, {"REC-010"}))
        self.assertTrue(all(c["calc_block_reason"][0]["code"] == "CAL-001" for c in a1["candidates"]))
        b1 = item(res, "B1")
        self.assertEqual((b1["biz_status"], codes(b1)), (C.ST_BILL_FAIL, {"REC-011", "CAL-005"}))
        self.assertEqual(b1["exception_candidate_type"], C.EX_DUP)
        c1 = item(res, "C1")
        self.assertEqual((c1["biz_status"], codes(c1)), (C.ST_PENDING, {"VAL-012", "CAL-005"}))
        d1 = item(res, "D1")
        self.assertEqual((d1["biz_status"], codes(d1)), (C.ST_BILL_FAIL, {"REC-012", "VAL-010"}))


class TC_D07_SurchargeDependency(unittest.TestCase):
    def test_TC_D07_base_not_unique_missing_or_unbilled(self):
        pipe, _i, _c, rc, rec = run_adhoc(self, [
            ["M1", "WB-71", "FS001", "10.00", P, "CNY"],
            ["N1", "WB-72", "FS001", "10.00", P, "CNY"],
            ["U1", "WB-73", "FS001", "10.00", P, "CNY"],
        ], [
            ["M-FR-1", "WB-71", "FR001", "4.00", "20.000", ""],
            ["M-FR-2", "WB-71", "FR001", "4.00", "20.000", ""],
            ["M-FS", "WB-71", "FS001", "", "", "0.125"],
            ["N-FS", "WB-72", "FS001", "", "", "0.125"],
            ["U-FR", "WB-73", "FR001", "4.00", "20.000", ""],
            ["U-FS", "WB-73", "FS001", "", "", "0.125"],
        ])
        res = pipe.results(rec)
        for rid in ("M1", "N1"):
            it = item(res, rid)
            self.assertEqual((it["biz_status"], it["output_amount"]), (C.ST_PENDING, None))
            self.assertIn("CAL-003", codes(it))
        u1 = item(res, "U1")
        self.assertEqual((u1["biz_status"], u1["output_amount"]), (C.ST_PENDING, "10.00"))
        self.assertIn("CAL-006", codes(u1))
        self.assertEqual(item(res, "U-FR")["biz_status"], C.ST_MISSED)


class TC_D08_MappingUnconfirmed(unittest.TestCase):
    def setUp(self):
        self.pipe = Pipeline(self)
        _c, imp = self.pipe.import_fixture("main")
        self.batch = imp["import_batch_id"]

    def _bad_mapping(self, mutate):
        with open(fx("main", "mapping_confirm.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        mutate(doc)
        path = os.path.join(self.pipe.workdir, "bad_mapping.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        return path

    def test_TC_D08_reconcile_refused_without_confirmation(self):
        for requested in ("", "not-a-hash", "a" * 64):
            code, out = self.pipe.reconcile(self.batch, requested)
            self.assertEqual((code, out["code"]), (C.EXIT_REFUSED, "MAP-001"))

    def test_TC_D08_invalid_confirmations_refused(self):
        main = fx("main", "mapping_confirm.json")
        cases = [
            (main, "SYSTEM", "2026-11-12T12:00:00+08:00", "MAP-002"),
            (main, "bot:auto", "2026-11-12T12:00:00+08:00", "MAP-002"),
            (main, "SIM-USER-MAP-A", "2026-11-12T12:00:00Z", "MAP-003"),
            (self._bad_mapping(lambda d: d["tables"]["SIM_BILL"].update(remark="tax_rate_x")), "SIM-USER-MAP-A",
             "2026-11-12T12:00:00+08:00", "MAP-003"),
            (self._bad_mapping(lambda d: d["tables"]["SIM_BILL"].pop("remark")), "SIM-USER-MAP-A",
             "2026-11-12T12:00:00+08:00", "MAP-003"),
            (self._bad_mapping(lambda d: d["tables"]["SIM_BILL"].update(remark="waybill_no")), "SIM-USER-MAP-A",
             "2026-11-12T12:00:00+08:00", "MAP-003"),
        ]
        for path, who, when, expected in cases:
            code, out = self.pipe.confirm(self.batch, path, by=who, at=when)
            self.assertEqual((code, out["code"]), (C.EXIT_REFUSED, expected), (who, when))
        self.assertFalse(os.path.exists(os.path.join(self.pipe.workdir, "batches", self.batch, "mapping")))


if __name__ == "__main__":
    unittest.main()
