"""Supplementary cases split out per PRD-CHG-2026-11-12-A (not part of the frozen baseline).

TC-16-S1 / TC-20-S1 / TC-21-S1 are R&D self-built checks. Whether they are adopted into an
evaluation version is decided by the evaluation lead; they do not count as independent evaluation.
"""

import csv
import io
import os
import shutil
import tempfile
import unittest

from helpers import C, Pipeline, codes, fx, item
from test_selfbuilt_tc_d01_d08 import P, run_adhoc


class TC16Supplement(unittest.TestCase):
    def test_TC_16_S1_adding_currency_only_removes_missing_column_obstacle(self):
        """DEC-SIM-C3-TC16: the added currency column only clears VAL-001; it proves no reconciliation success."""
        pipe = Pipeline(self)
        code, imp = pipe.import_fixture("tc16_map_supplement")
        self.assertEqual(code, 0)
        code, out = pipe.reconcile(imp["import_batch_id"], "")
        self.assertEqual(out["code"], "MAP-001")
        code, conf = pipe.confirm(imp["import_batch_id"], fx("tc16_map_supplement", "mapping_confirm.json"))
        self.assertEqual(code, 0)
        code, rec = pipe.reconcile(imp["import_batch_id"], conf["mapping_hash"])
        res = pipe.results(rec)
        self.assertNotIn("VAL-001", [r["code"] for r in res["batch_block_reasons"]])
        self.assertEqual(res["mapping"]["mapping_hash"], conf["mapping_hash"])
        row = item(res, "L2", "SIM_BILL")
        self.assertEqual(row["currency"], "CNY")
        self.assertIn(row["biz_status"], (C.ST_PASS, C.ST_BILL_FAIL, C.ST_MISSED, C.ST_PENDING))
        self.assertNotEqual(row["block_scope"], C.SCOPE_BATCH)


class TC20Supplement(unittest.TestCase):
    def _write(self, header, rows):
        d = tempfile.mkdtemp(prefix="frecon-s1-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "SIM_WAYBILL.csv")
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows([header] + rows)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(buf.getvalue())
        return path

    def test_TC_20_S1_imp004_only_for_unregistered_headers(self):
        """DEC-SIM-C1-VAL001: IMP-004 = unregistered header only; a missing required column is not IMP-004."""
        pipe = Pipeline(self)
        unregistered = self._write(["source_row_id", "waybill_no", "charge_code", "unit_price", "chargeable_weight",
                                    "freight_secret_col"], [["X1", "WB-S1", "FR001", "5.00", "10.000", "1"]])
        code, out = pipe.import_(fx("tc20_col", "SIM_BILL.csv"), unregistered, "IDEMP-S1-UNREG")
        self.assertEqual((code, out["error"]["code"]), (C.EXIT_REFUSED, "IMP-004"))
        self.assertEqual(out["error"]["detail"]["unregistered_headers"], ["freight_secret_col"])
        self.assertFalse(os.path.exists(os.path.join(pipe.workdir, "batches")))
        code, ok = pipe.import_fixture("tc20_col", key="IDEMP-S1-MISSINGCOL")
        self.assertEqual((code, ok["ingest_status"]), (0, "accepted"))
        self.assertEqual(C.CODES["IMP-004"], "表头含未登记字段")


class TC21Supplement(unittest.TestCase):
    def test_TC_21_S1_failed_base_never_overwritten_and_surcharge_never_passes(self):
        """DEC-SIM-C6-CAL006: computable surcharge evidence kept, base failure kept, no pass candidate."""
        pipe, _i, _c, rc, rec = run_adhoc(self, [
            ["G01", "WB-S21", "FR001", "132.00", P, "CNY"],
            ["G02", "WB-S21", "FS001", "99.99", P, "CNY"],
        ], [
            ["GW01", "WB-S21", "FR001", "5.00", "25.100", ""],
            ["GW02", "WB-S21", "FS001", "", "", "0.125"],
        ])
        res = pipe.results(rec)
        g01, g02 = item(res, "G01"), item(res, "G02")
        self.assertEqual((g01["biz_status"], codes(g01)), (C.ST_BILL_FAIL, {"CAL-005"}))
        self.assertEqual((g02["output_amount"], g02["inputs"]["A_FR001"]["value"]), ("15.69", "125.50"))
        self.assertEqual(g02["biz_status"], C.ST_PENDING)
        self.assertEqual(codes(g02), {"CAL-006", "CAL-005"})


if __name__ == "__main__":
    unittest.main()
