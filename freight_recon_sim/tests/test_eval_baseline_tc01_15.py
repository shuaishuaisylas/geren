"""Developer self-tests for EVAL-TD-BASELINE-2026-11-12-R1 TC-01..TC-15 (original 15 cases).

These are R&D self-tests that replay the baseline cases through the real CLI. They are NOT the
independent evaluation; the independent evaluation status remains "未执行".
"""

import csv
import io
import json
import os
import unittest

from helpers import C, Pipeline, codes, item, missing_evidence, sensitive_tokens


class MainBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pipe = Pipeline(cls._dummy())
        cls.main_run = cls.pipe.run_fixture("main")
        cls.res = cls.main_run["results"]
        run_id = cls.main_run["rec"]["reconcile_run_id"]
        cls.exp_json = cls.pipe.export(cls.main_run["batch"], run_id, "json")
        cls.exp_csv = cls.pipe.export(cls.main_run["batch"], run_id, "csv")

    @classmethod
    def tearDownClass(cls):
        cls.pipe._cleanup()

    @staticmethod
    def _dummy():
        class _T:
            def addCleanup(self, *_a):
                pass
        return _T()

    def export_items(self):
        with open(os.path.join(self.pipe.workdir, self.exp_json[1]["path"]), encoding="utf-8") as fh:
            return {i["source_row_id"]: i for i in json.load(fh)["items"]}

    def assert_pass_candidate(self, it, amount):
        self.assertEqual(it["biz_status"], C.ST_PASS)
        self.assertEqual(it["output_amount"], amount)
        self.assertEqual(it["diff_amount"], "0.00")
        self.assertEqual(it["block_scope"], C.SCOPE_NONE)
        self.assertEqual(it["match_cardinality"], 1)
        self.assertEqual(missing_evidence(it), [])
        self.assertIsNone(it["human_review_record"])

    def test_pipeline_completed_row_level(self):
        self.assertEqual(self.main_run["exit"], C.EXIT_OK)
        self.assertEqual(self.res["batch_status"], C.BATCH_OK)
        self.assertEqual(self.res["rule_snapshot"]["rule_version"], "RV-SIM-2026-11-12-A")
        self.assertEqual(self.res["independent_eval_status"], C.INDEPENDENT_EVAL_STATUS)

    def test_TC_01_exact_unique_match_consistent(self):
        it = item(self.res, "B01", "SIM_BILL")
        self.assert_pass_candidate(it, "125.50")
        self.assertEqual(it["candidate_row_ids"], ["SIM_WAYBILL.W01"])
        self.assertEqual(it["match_key"], "WB-SIM-0001|FR001")
        self.assertEqual(it["intermediates"], {"P×W": "125.50000"})
        self.assertEqual((it["rounding_mode"], it["rounding_scale"]), ("ROUND_HALF_UP", 2))
        self.assertEqual(it["formula_id"], "(1)")
        self.assertEqual(it["approval_status"], "模拟约定")

    def test_TC_02_bill_side_duplicate(self):
        for rid, other in (("B02", "SIM_BILL.B03"), ("B03", "SIM_BILL.B02")):
            it = item(self.res, rid, "SIM_BILL")
            self.assertEqual(it["biz_status"], C.ST_BILL_FAIL)
            self.assertIn("REC-011", codes(it))
            self.assertEqual(it["exception_candidate_type"], C.EX_DUP)
            self.assertEqual(it["related_bill_row_ids"], [other])
            self.assertEqual(it["output_amount"], "80.00")
            self.assertEqual(it["intermediates"], {"P×W": "80.00000"})

    def test_TC_03_bill_without_waybill(self):
        it = item(self.res, "B04", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_BILL_FAIL)
        self.assertEqual(codes(it), {"REC-012"})
        self.assertEqual(it["completeness_flag"], C.CF_NO_COUNTERPART)
        self.assertEqual(it["exception_candidate_type"], C.EX_DOC_MISSING)
        self.assertIsNone(it["output_amount"])
        self.assertIsNone(it["diff_amount"])
        self.assertEqual(it["bill_amount"], "60.00")

    def test_TC_04_waybill_without_bill(self):
        it = item(self.res, "W03", "SIM_WAYBILL")
        self.assertEqual(it["biz_status"], C.ST_MISSED)
        self.assertIn("REC-013", codes(it))
        self.assertEqual(it["output_amount"], "50.00")
        self.assertEqual(it["intermediates"], {"P×W": "50.00000"})
        self.assertEqual(it["completeness_flag"], C.CF_NO_COUNTERPART)
        self.assertEqual(it["exception_candidate_type"], C.EX_MISSED)
        self.assertIsNone(it["bill_amount"])

    def test_TC_05_amount_mismatch(self):
        it = item(self.res, "B05", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_BILL_FAIL)
        self.assertEqual((it["output_amount"], it["diff_amount"]), ("125.50", "6.50"))
        self.assertIn("CAL-005", codes(it))
        self.assertEqual(it["exception_candidate_type"], C.EX_AMOUNT)
        self.assertIsNone(it["ai_explanation"])
        self.assertEqual(it["ai_explanation_status"], "skipped")

    def test_TC_06_rule_missing_no_fallback(self):
        it = item(self.res, "B06", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_PENDING)
        self.assertEqual(codes(it), {"CAL-001"})
        self.assertIsNone(it["output_amount"])
        self.assertIsNone(it["rule_id"])
        self.assertIsNone(it["formula_id"])
        self.assertEqual(it["intermediates"], {})

    def test_TC_07_missing_chargeable_weight(self):
        it = item(self.res, "B07", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_PENDING)
        self.assertEqual(codes(it), {"CAL-002"})
        self.assertIn("chargeable_weight", it["missing_fields"])
        self.assertIsNone(it["inputs"]["W"]["value"])
        self.assertIsNone(it["output_amount"])
        self.assertEqual(it["block_scope"], C.SCOPE_ROW)

    def test_TC_08_multiple_waybill_candidates(self):
        it = item(self.res, "B08", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_PENDING)
        self.assertIn("REC-010", codes(it))
        self.assertEqual(it["match_cardinality"], 2)
        self.assertEqual(it["candidate_row_ids"], ["SIM_WAYBILL.W07", "SIM_WAYBILL.W08"])
        outs = {c["source_row_id"]: c["output_amount"] for c in it["candidates"]}
        self.assertEqual(outs, {"SIM_WAYBILL.W07": "100.00", "SIM_WAYBILL.W08": "90.00"})
        self.assertIsNone(it["output_amount"])
        self.assertIsNone(it["diff_amount"])

    def test_TC_09_rounding_boundary_consistent(self):
        it = item(self.res, "B09", "SIM_BILL")
        self.assert_pass_candidate(it, "1.01")
        self.assertEqual(it["intermediates"], {"P×W": "1.00500"})

    def test_TC_10_rounding_boundary_truncated_bill(self):
        it = item(self.res, "B10", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_BILL_FAIL)
        self.assertEqual((it["output_amount"], it["diff_amount"]), ("1.01", "-0.01"))
        self.assertEqual(it["intermediates"], {"P×W": "1.00500"})
        self.assertEqual(it["rounding_mode"], "ROUND_HALF_UP")

    def test_TC_11_evidence_chain_complete(self):
        it = item(self.res, "B11", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_BILL_FAIL)
        self.assertEqual(it["diff_amount"], "5.00")
        exported = self.export_items()["B11"]
        self.assertEqual(missing_evidence(exported), [])
        self.assertEqual(exported["source_row_id"], "B11")
        self.assertEqual(exported["candidate_row_ids"], ["SIM_WAYBILL.W11"])
        self.assertEqual(exported["match_key"], "WB-SIM-0011|FR001")
        self.assertEqual(exported["rule_version"], "RV-SIM-2026-11-12-A")
        self.assertEqual(exported["inputs"]["P"]["value"], "7.50")
        self.assertEqual(exported["inputs"]["W"]["value"], "10.000")
        self.assertEqual(exported["formula_id"], "(1)")
        self.assertEqual(exported["intermediates"], {"P×W": "75.00000"})
        self.assertEqual(exported["output_amount"], "75.00")
        self.assertIsNone(exported["human_review_record"])
        with open(os.path.join(self.pipe.workdir, self.exp_csv[1]["path"]), encoding="utf-8") as fh:
            header = next(csv.reader(io.StringIO(fh.read())))
        self.assertEqual([f for f in C.EVIDENCE_REQUIRED_FIELDS if f not in header], [])

    def test_TC_12_sensitive_output_suppressed(self):
        it = item(self.res, "B12", "SIM_BILL")
        self.assert_pass_candidate(it, "50.00")
        tokens = sensitive_tokens("main")
        self.assertGreaterEqual(len(tokens), 3)
        text = self.pipe.all_output_text()
        leaked = [t for t in tokens if t in text]
        self.assertEqual(leaked, [], "simulated sensitive placeholders leaked into observable output")
        self.assertNotIn("remark", json.dumps(self.res["items"], ensure_ascii=False))

    def test_TC_13_missing_waybill_no_no_fuzzy_match(self):
        it = item(self.res, "B13", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_PENDING)
        self.assertEqual(codes(it), {"VAL-014"})
        self.assertEqual(it["missing_fields"], ["waybill_no"])
        self.assertEqual(it["candidate_row_ids"], [])
        self.assertIsNone(it["match_key"])
        self.assertIsNone(it["output_amount"])
        w13 = item(self.res, "W13", "SIM_WAYBILL")
        self.assertNotIn("SIM_BILL.B13", json.dumps(w13, ensure_ascii=False))
        self.assertNotEqual(w13["biz_status"], C.ST_PASS)

    def test_TC_14_single_final_rounding(self):
        it = item(self.res, "B14", "SIM_BILL")
        self.assertEqual(it["biz_status"], C.ST_BILL_FAIL)
        self.assertEqual((it["output_amount"], it["diff_amount"]), ("11.03", "-0.01"))
        self.assertEqual(it["intermediates"]["P×W"], "10.02330")
        self.assertEqual(it["intermediates"]["P×W×(1+R)"], "11.0256300")
        self.assertEqual(it["formula_id"], "(3)")

    def test_TC_15_two_charges_same_waybill(self):
        fr = item(self.res, "B15", "SIM_BILL")
        fs = item(self.res, "B16", "SIM_BILL")
        self.assert_pass_candidate(fr, "80.00")
        self.assert_pass_candidate(fs, "10.00")
        self.assertEqual(fs["inputs"]["A_FR001"]["value"], "80.00")
        self.assertTrue(fs["inputs"]["A_FR001"]["rounded"])
        self.assertIn("SIM_WAYBILL.W15", fs["base_amount_source"])
        self.assertEqual(fs["intermediates"], {"A_FR001×R": "10.00000"})
        self.assertNotEqual(fr["line_uid"], fs["line_uid"])

    def test_exports_have_no_pass_without_label(self):
        manifest = self.exp_json[1]
        self.assertTrue(manifest["redaction_applied"])
        self.assertEqual(manifest["independent_eval_status"], C.INDEPENDENT_EVAL_STATUS)
        for it in self.export_items().values():
            self.assertEqual(it["data_label"], C.DATA_LABEL)


if __name__ == "__main__":
    unittest.main()
