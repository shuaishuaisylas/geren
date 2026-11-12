"""Developer self-tests for EVAL-TD-BASELINE-2026-11-12-R1 TC-16..TC-29.

R&D self-tests only; independent evaluation remains "未执行".
"""

import builtins
import glob
import json
import os
import re
import shutil
import socket
import tempfile
import unittest
from unittest import mock

from helpers import C, ROOT, Pipeline, codes, fx, item, missing_evidence
from frecon.util import sha256_file


def _no_runs(pipe, batch):
    return not glob.glob(os.path.join(pipe.workdir, "batches", batch, "runs", "*"))


class TC16MappingGate(unittest.TestCase):
    def setUp(self):
        self.pipe = Pipeline(self)
        code, imp = self.pipe.import_fixture("tc16_map")
        self.assertEqual(code, 0, imp)
        self.batch = imp["import_batch_id"]

    def test_TC_16_A_ai_candidate_only_blocks(self):
        code, val = self.pipe.validate(self.batch, fx("tc16_map", "ai_candidate_MAP-CAND-SIM-01.json"))
        self.assertEqual(code, 0)
        self.assertTrue(all(p["mapping_status"] == "proposed" for p in val["mapping_proposals"]))
        sys_prop = val["mapping_proposals"][0]
        self.assertEqual(sys_prop["ambiguous_columns"]["SIM_WAYBILL"]["重量"], ["chargeable_weight", "actual_weight"])
        ai_hash = val["mapping_proposals"][1]["mapping_hash"]
        for requested in ("", ai_hash, sys_prop["mapping_hash"]):
            code, out = self.pipe.reconcile(self.batch, requested)
            self.assertEqual(code, C.EXIT_REFUSED)
            self.assertEqual(out["code"], "MAP-001")
            self.assertFalse(out["gate_record"]["auto_match_executed"])
            self.assertFalse(out["gate_record"]["calculation_executed"])
        self.assertTrue(_no_runs(self.pipe, self.batch))
        for who in ("AI", "MAP-CAND-SIM-01", "system", "AI-mapper"):
            code, out = self.pipe.confirm(self.batch, fx("tc16_map", "mapping_confirm.json"), by=who)
            self.assertEqual((code, out["code"]), (C.EXIT_REFUSED, "MAP-002"))

    def test_TC_16_B_confirmation_bound_to_person_time_version(self):
        code, conf = self.pipe.confirm(self.batch, fx("tc16_map", "mapping_confirm.json"))
        self.assertEqual(code, 0, conf)
        self.assertEqual((conf["confirmed_by"], conf["confirmed_at"], conf["mapping_version"]),
                         ("SIM-USER-MAP-A", "2026-11-12T12:00:00+08:00", "MAP-SIM-2026-11-12-01"))
        code, rec = self.pipe.reconcile(self.batch, conf["mapping_hash"])
        res = self.pipe.results(rec)
        self.assertEqual(res["mapping"]["mapping_hash"], conf["mapping_hash"])
        self.assertEqual(res["mapping"]["confirmed_by"], "SIM-USER-MAP-A")
        # Baseline fixture headers have no currency column; the template requires currency -> VAL-001.
        self.assertEqual(code, C.EXIT_BATCH_BLOCKED)
        self.assertEqual(res["batch_block_reasons"][0]["code"], "VAL-001")
        self.assertEqual(res["batch_block_reasons"][0]["detail"]["missing_columns"], {"SIM_BILL": ["currency"]})

    def test_TC_16_B_supplement_confirmed_mapping_reaches_reconcile(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("tc16_map_supplement")
        self.assertEqual(run["exit"], 0)
        it = item(run["results"], "L2", "SIM_BILL")
        self.assertEqual((it["biz_status"], it["output_amount"]), (C.ST_PASS, "50.00"))

    def test_TC_16_C_changed_mapping_without_reconfirmation_blocks(self):
        code, conf = self.pipe.confirm(self.batch, fx("tc16_map", "mapping_confirm.json"))
        path = os.path.join(self.pipe.workdir, "batches", self.batch, "mapping", f"{conf['mapping_hash']}.json")
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["tables"]["SIM_WAYBILL"]["重量"] = "actual_weight"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        code, out = self.pipe.reconcile(self.batch, conf["mapping_hash"])
        self.assertEqual((code, out["code"]), (C.EXIT_REFUSED, "MAP-001"))
        self.assertTrue(_no_runs(self.pipe, self.batch))
        changed = os.path.join(self.pipe.workdir, "changed_mapping.json")
        with open(fx("tc16_map", "mapping_confirm.json"), encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["tables"]["SIM_WAYBILL"]["重量"] = "actual_weight"
        doc["mapping_version"] = "MAP-SIM-2026-11-12-02"
        with open(changed, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        code, out = self.pipe.reconcile(self.batch, "f" * 64)
        self.assertEqual(out["code"], "MAP-001")
        code, conf2 = self.pipe.confirm(self.batch, changed)
        self.assertEqual(code, 0)
        self.assertNotEqual(conf2["mapping_hash"], conf["mapping_hash"])
        code, rec = self.pipe.reconcile(self.batch, conf2["mapping_hash"])
        miss = self.pipe.results(rec)["batch_block_reasons"][0]["detail"]["missing_columns"]
        self.assertIn("chargeable_weight", miss["SIM_WAYBILL"])


class TC17ReadOnlySource(unittest.TestCase):
    def test_TC_17_source_files_untouched_derived_copy_only(self):
        src = tempfile.mkdtemp(prefix="frecon-src-")
        self.addCleanup(shutil.rmtree, src, True)
        for name in ("SIM_BILL.csv", "SIM_WAYBILL.csv", "mapping_confirm.json"):
            shutil.copy(fx("tc17_ro", name), os.path.join(src, name))
        bill, wb = os.path.join(src, "SIM_BILL.csv"), os.path.join(src, "SIM_WAYBILL.csv")
        for path in (bill, wb):
            os.chmod(path, 0o444)
        before = {p: sha256_file(p) for p in (bill, wb)}
        mtimes = {p: os.stat(p).st_mtime_ns for p in (bill, wb)}
        write_attempts = []
        real_open, real_os_open = builtins.open, os.open

        def guarded_open(file, mode="r", *a, **kw):
            if os.path.abspath(str(file)) in before and any(m in mode for m in "wax+"):
                write_attempts.append(file)
            return real_open(file, mode, *a, **kw)

        def guarded_os_open(path, flags, *a, **kw):
            if os.path.abspath(str(path)) in before and flags & (os.O_WRONLY | os.O_RDWR):
                write_attempts.append(path)
            return real_os_open(path, flags, *a, **kw)

        pipe = Pipeline(self, inproc=True)
        with mock.patch("builtins.open", guarded_open), mock.patch("os.open", guarded_os_open):
            code, imp = pipe.import_(bill, wb, "IDEMP-TC17")
            batch = imp["import_batch_id"]
            code, conf = pipe.confirm(batch, os.path.join(src, "mapping_confirm.json"))
            code, rec = pipe.reconcile(batch, conf["mapping_hash"])
        self.assertEqual(write_attempts, [])
        self.assertEqual({p: sha256_file(p) for p in (bill, wb)}, before)
        self.assertEqual({p: os.stat(p).st_mtime_ns for p in (bill, wb)}, mtimes)
        res = pipe.results(rec)
        integ = res["source_integrity"]["SIM_BILL"]
        self.assertTrue(integ["file_unchanged"] and integ["row_digests_unchanged"])
        self.assertEqual(integ["file_sha256_at_import"], before[bill])
        r01 = item(res, "R01", "SIM_BILL")
        self.assertEqual((r01["waybill_no"], r01["biz_status"], r01["output_amount"]),
                         ("WB-SIM-0102", C.ST_PASS, "125.50"))
        derived_path = glob.glob(os.path.join(pipe.workdir, "batches", batch, "runs", "*", "derived",
                                              "*SIM_BILL.json"))[0]
        with open(derived_path, encoding="utf-8") as fh:
            derived = json.load(fh)
        self.assertEqual(derived["lineage"]["source_file_sha256"], before[bill])
        self.assertIn({"source_row_id": "R01", "field": "waybill_no", "transform": "strip_whitespace"},
                      derived["transform_log"])
        self.assertNotEqual(sha256_file(derived_path), before[bill])
        self.assertEqual(res["derived_copies"]["SIM_BILL"]["derived_sha256"], sha256_file(derived_path))
        code, out = pipe.cli("request-action", "--workdir", pipe.workdir, "--action", "source:write")
        self.assertEqual((code, out["decision"], out["written"]), (C.EXIT_REFUSED, "rejected", 0))


class _ScopeBase(unittest.TestCase):
    fixture = None
    actions = ()
    perm = None
    mock_name = None

    def check(self):
        pipe = Pipeline(self, inproc=True)
        connects = []
        with mock.patch.object(socket.socket, "connect", lambda *a, **k: connects.append(a)), \
                mock.patch("socket.create_connection", lambda *a, **k: connects.append(a)):
            run = pipe.run_fixture(self.fixture)
            for action in self.actions:
                code, out = pipe.cli("request-action", "--workdir", pipe.workdir, "--action", action, "--note",
                                     "模拟诱导请求（超范围文本）")
                self.assertEqual((code, out["decision"], out["code"]), (C.EXIT_REFUSED, "rejected", "OOS-001"))
            code, perms = pipe.cli("permissions", "--workdir", pipe.workdir)
        self.assertEqual(connects, [])
        self.assertEqual(perms["permissions"][self.perm]["allowed"], [])
        counters = perms["mock_counters"][self.mock_name]
        self.assertEqual((counters["attempts"], counters["rejected"], counters["successful"]),
                         (len(self.actions), len(self.actions), 0))
        self.assertEqual(run["results"]["side_effects"],
                         {"source_files_written": 0, "ledger_posts": 0, "payments": 0, "network_calls": 0})
        self.assertNotIn("模拟诱导语", pipe.all_output_text())
        return run


class TC18NoLedger(_ScopeBase):
    fixture, perm, mock_name = "tc18_ledger", "SIM_PERM_LEDGER", "SIM-MOCK-LEDGER"
    actions = ("ledger:post", "voucher:create", "account:write")

    def test_TC_18_ledger_requests_refused(self):
        run = self.check()
        self.assertEqual(item(run["results"], "L01")["output_amount"], "50.00")


class TC19NoPayment(_ScopeBase):
    fixture, perm, mock_name = "tc19_pay", "SIM_PERM_PAY", "SIM-MOCK-PAY"
    actions = ("payment:create", "bank:transfer", "payout:execute")

    def test_TC_19_payment_requests_refused(self):
        run = self.check()
        self.assertNotIn("SIM-PAYEE-0001", json.dumps(run["results"], ensure_ascii=False))


class TC20WholeColumnMissing(unittest.TestCase):
    def test_TC_20_batch_block_then_reconfirm_and_rerun(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("tc20_col", key="IDEMP-TC20")
        self.assertEqual(run["exit"], C.EXIT_BATCH_BLOCKED)
        res = run["results"]
        self.assertEqual(res["batch_status"], C.BATCH_BLOCKED)
        reason = res["batch_block_reasons"][0]
        self.assertEqual((reason["code"], reason["detail"]["missing_columns"]),
                         ("VAL-001", {"SIM_WAYBILL": ["chargeable_weight"]}))
        self.assertEqual(len(res["items"]), 6)
        for it in res["items"]:
            self.assertEqual((it["block_scope"], it["biz_status"]), (C.SCOPE_BATCH, C.ST_PENDING))
            self.assertIsNone(it["output_amount"])
            self.assertIsNone(it["diff_amount"])
            self.assertIsNone(it["match_key"])
        code, exp = pipe.export(run["batch"], run["rec"]["reconcile_run_id"], "json")
        self.assertEqual((exp["pass_candidate_count"], exp["batch_status"]), (0, C.BATCH_BLOCKED))
        old_results_sha = run["rec"]["results_sha256"]

        code, out = pipe.import_fixture("tc20_col_fixed", key="IDEMP-TC20")
        self.assertEqual((code, out["error"]["code"]), (C.EXIT_REFUSED, "IMP-007"))
        code, fixed = pipe.import_fixture("tc20_col_fixed", key="IDEMP-TC20-FIX-1")
        self.assertNotEqual(fixed["import_batch_id"], run["batch"])
        code, out = pipe.reconcile(fixed["import_batch_id"], run["mapping"]["mapping_hash"])
        self.assertEqual(out["code"], "MAP-001")
        code, conf = pipe.confirm(fixed["import_batch_id"], fx("tc20_col_fixed", "mapping_confirm.json"))
        code, rec = pipe.reconcile(fixed["import_batch_id"], conf["mapping_hash"])
        self.assertEqual(code, 0)
        self.assertEqual(rec["summary"]["by_biz_status"], {C.ST_PASS: 3})
        code, again = pipe.reconcile(run["batch"], run["mapping"]["mapping_hash"])
        self.assertEqual((again["results_sha256"], again["replayed"]), (old_results_sha, True))

    def test_TC_20_distinct_from_row_level_TC_07(self):
        pipe = Pipeline(self)
        main = pipe.run_fixture("main")
        self.assertEqual(item(main["results"], "B07")["block_scope"], C.SCOPE_ROW)


class TC21SurchargeDependency(unittest.TestCase):
    def test_TC_21_A_base_unavailable(self):
        res = Pipeline(self).run_fixture("tc21_dep_a")["results"]
        f01, f02 = item(res, "F01"), item(res, "F02")
        self.assertEqual((f01["biz_status"], codes(f01)), (C.ST_PENDING, {"CAL-002"}))
        self.assertEqual(f02["biz_status"], C.ST_PENDING)
        self.assertIn("CAL-003", codes(f02))
        self.assertIsNone(f02["output_amount"])
        self.assertIsNone(f02["diff_amount"])
        self.assertNotIn("11.25", json.dumps(f02["intermediates"]))
        self.assertEqual(missing_evidence(f02), [])

    def test_TC_21_B_base_row_failed_surcharge_not_pass(self):
        res = Pipeline(self).run_fixture("tc21_dep_b")["results"]
        g01, g02 = item(res, "G01"), item(res, "G02")
        self.assertEqual((g01["biz_status"], g01["diff_amount"]), (C.ST_BILL_FAIL, "6.50"))
        self.assertEqual(g02["output_amount"], "15.69")
        self.assertEqual(g02["intermediates"], {"A_FR001×R": "15.68750"})
        self.assertEqual(g02["inputs"]["A_FR001"]["value"], "125.50")
        self.assertIn("SIM_WAYBILL.GW01", g02["base_amount_source"])
        self.assertNotEqual(g02["biz_status"], C.ST_PASS)
        self.assertEqual(g02["biz_status"], C.ST_PENDING)
        self.assertIn("CAL-006", codes(g02))


class TC22Delimiter(unittest.TestCase):
    def test_TC_22_delimiter_collision_no_split_no_cross_match(self):
        res = Pipeline(self).run_fixture("tc22_delim")["results"]
        d00, d01 = item(res, "D00"), item(res, "D01")
        self.assertEqual((d00["biz_status"], d00["candidate_row_ids"]), (C.ST_PASS, ["SIM_WAYBILL.WD00"]))
        self.assertEqual((d01["biz_status"], codes(d01)), (C.ST_PENDING, {"VAL-014"}))
        self.assertEqual((d01["waybill_no"], d01["charge_code"]), ("WB-SIM-0301|X", "FR001"))
        self.assertEqual(d01["candidate_row_ids"], [])
        wd01 = item(res, "WD01")
        self.assertEqual((wd01["biz_status"], codes(wd01)), (C.ST_PENDING, {"VAL-014"}))
        self.assertFalse(any(i["charge_code"] == "X" for i in res["items"]))


class TC23NoEval(unittest.TestCase):
    def _run(self, stage, version):
        pipe = Pipeline(self, inproc=True)
        calls = []
        real_eval, real_exec = builtins.eval, builtins.exec
        with mock.patch("builtins.eval", lambda *a, **k: calls.append("eval") or real_eval(*a, **k)), \
                mock.patch("builtins.exec", lambda *a, **k: calls.append("exec") or real_exec(*a, **k)):
            run = pipe.run_fixture(f"tc23_formula_{stage}", rules=fx("rules", f"{version}.catalog.json"),
                                   snapshot=fx("rules", f"{version}.snapshot.json"), version=version,
                                   sha=sha256_file(fx("rules", f"{version}.catalog.json")))
        self.assertEqual(calls, [])
        return run["results"]

    def test_TC_23_A_unregistered_formula_id(self):
        it = item(self._run("a", "RV-SIM-NEG-TC23A"), "E01")
        self.assertEqual((it["biz_status"], codes(it)), (C.ST_PENDING, {"RUL-001"}))
        self.assertIsNone(it["output_amount"])
        self.assertEqual(it["intermediates"], {})

    def test_TC_23_B_free_expression_refused(self):
        it = item(self._run("b", "RV-SIM-NEG-TC23B"), "E02")
        self.assertEqual((it["biz_status"], codes(it)), (C.ST_PENDING, {"RUL-002"}))
        self.assertIsNone(it["output_amount"])

    def test_TC_23_static_no_dynamic_evaluation_in_engine(self):
        for path in glob.glob(os.path.join(ROOT, "frecon", "*.py")):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for pattern in (r"(?<![\w.])eval\(", r"(?<![\w.])exec\(", r"(?<![\w.])compile\(", r"__import__",
                            r"\bsubprocess\b", r"os\.system", r"importlib"):
                self.assertIsNone(re.search(pattern, text), f"{pattern} in {path}")


class TC24FormulaInjection(unittest.TestCase):
    def test_TC_24_A_formula_prefixes_rejected(self):
        pipe = Pipeline(self)
        rejection_ids = set()
        for tag, prefix in (("eq", "="), ("plus", "+"), ("at", "@"), ("minus", "-")):
            code, out = pipe.import_fixture(f"tc24_inj_{tag}")
            self.assertEqual(code, C.EXIT_REFUSED)
            self.assertEqual(out["ingest_status"], "rejected")
            self.assertEqual(out["error"]["code"], "IMP-005")
            self.assertEqual(out["block_scope"], C.SCOPE_BATCH)
            self.assertEqual([f["prefix"] for f in out["error"]["detail"]["findings"]], [prefix])
            rejection_ids.add(out["rejection_id"])
            code, exp = pipe.export(out["rejection_id"], "RR-NONE", "json")
            self.assertEqual(exp["code"], "EXP-001")
        self.assertEqual(len(rejection_ids), 4)
        self.assertFalse(os.path.exists(os.path.join(pipe.workdir, "batches")))
        self.assertNotIn("CMD-SIM-NOT-RUN", pipe.all_output_text())

    def test_TC_24_B_negative_amount_accepted(self):
        res = Pipeline(self).run_fixture("tc24_negative")["results"]
        n01 = item(res, "N01")
        self.assertEqual(n01["bill_amount"], "-0.01")
        self.assertNotIn("VAL-011", codes(n01))
        self.assertEqual((n01["output_amount"], n01["diff_amount"]), ("50.00", "-50.01"))


class TC25Idempotency(unittest.TestCase):
    def test_TC_25_replay_same_and_reject_changed(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("tc25_idem", key="IDEMP-SIM-0305")
        batch = run["batch"]
        batch_json = os.path.join(pipe.workdir, "batches", batch, "batch.json")
        before = (sha256_file(batch_json), os.stat(batch_json).st_mtime_ns)
        code, again = pipe.import_fixture("tc25_idem", key="IDEMP-SIM-0305")
        self.assertEqual((code, again["import_batch_id"], again["replayed"]), (0, batch, True))
        code, rec2 = pipe.reconcile(batch, run["mapping"]["mapping_hash"])
        self.assertEqual((rec2["reconcile_run_id"], rec2["results_sha256"], rec2["replayed"]),
                         (run["rec"]["reconcile_run_id"], run["rec"]["results_sha256"], True))
        code, out = pipe.import_fixture("tc25_idem_changed", key="IDEMP-SIM-0305")
        self.assertEqual((code, out["error"]["code"]), (C.EXIT_REFUSED, "IMP-007"))
        self.assertEqual(out["error"]["detail"]["existing_import_batch_id"], batch)
        self.assertEqual((sha256_file(batch_json), os.stat(batch_json).st_mtime_ns), before)
        self.assertEqual(sha256_file(os.path.join(pipe.workdir, run["rec"]["results_path"])),
                         run["rec"]["results_sha256"])
        self.assertEqual(len(os.listdir(os.path.join(pipe.workdir, "batches"))), 1)


class TC26MultiErrorMerge(unittest.TestCase):
    def test_TC_26_duplicate_and_multi_candidate(self):
        res = Pipeline(self).run_fixture("tc26_multi")["results"]
        for rid in ("J01", "J02"):
            it = item(res, rid)
            self.assertEqual(it["biz_status"], C.ST_PENDING)
            self.assertTrue({"REC-010", "REC-011"} <= codes(it))
            self.assertEqual(it["exception_candidate_type"], C.EX_DUP)
            self.assertEqual(it["candidate_row_ids"], ["SIM_WAYBILL.WJ01", "SIM_WAYBILL.WJ02"])
            self.assertEqual({c["output_amount"] for c in it["candidates"]}, {"100.00", "90.00"})
            self.assertIsNone(it["output_amount"])
        self.assertEqual(item(res, "J01")["related_bill_row_ids"], ["SIM_BILL.J02"])


class TC27EncodingFormat(unittest.TestCase):
    def test_TC_27_encoding_and_format_failures_distinct(self):
        pipe = Pipeline(self)
        code_a, enc = pipe.import_fixture("tc27_enc", key="IDEMP-TC27-ENC")
        code_b, enc_decl = pipe.import_fixture("tc27_enc", key="IDEMP-TC27-ENC-DECL", encoding="GBK")
        code_c, fmt = pipe.import_fixture("tc27_fmt", key="IDEMP-TC27-FMT")
        self.assertEqual((code_a, enc["error"]["code"]), (C.EXIT_REFUSED, "IMP-001"))
        self.assertEqual((code_b, enc_decl["error"]["code"]), (C.EXIT_REFUSED, "IMP-001"))
        self.assertEqual((code_c, fmt["error"]["code"]), (C.EXIT_REFUSED, "IMP-002"))
        self.assertEqual(enc["error"]["detail"]["source_table"], "SIM_BILL")
        self.assertEqual(len({enc["rejection_id"], enc_decl["rejection_id"], fmt["rejection_id"]}), 3)
        for out in (enc, enc_decl, fmt):
            self.assertEqual((out["ingest_status"], out["block_scope"], out["reconcile_allowed"]),
                             ("rejected", C.SCOPE_BATCH, False))
        self.assertFalse(os.path.exists(os.path.join(pipe.workdir, "batches")))


class TC28Catalog(unittest.TestCase):
    def _blocked(self, run, code, trigger=None):
        self.assertEqual(run["exit"], C.EXIT_BATCH_BLOCKED)
        res = run["results"]
        reasons = res["batch_block_reasons"]
        self.assertEqual([r["code"] for r in reasons], [code])
        if trigger:
            self.assertEqual(reasons[0]["detail"]["trigger"], trigger)
        self.assertEqual(res["summary"]["by_biz_status"], {C.ST_PENDING: 2})
        self.assertTrue(all(i["block_scope"] == C.SCOPE_BATCH for i in res["items"]))
        self.assertEqual({i["source_row_id"] for i in res["items"]}, {"K01", "WK01"})

    def test_TC_28_LOAD_missing_catalog(self):
        run = Pipeline(self).run_fixture("tc28_catalog", rules=fx("rules", "does-not-exist.catalog.json"))
        self._blocked(run, "VAL-002", "LOAD")

    def test_TC_28_LOAD_malformed_catalog(self):
        run = Pipeline(self).run_fixture("tc28_catalog", rules=fx("rules", "malformed.catalog.json"))
        self._blocked(run, "VAL-002", "LOAD")

    def test_TC_28_UNFROZEN_catalog_without_snapshot(self):
        run = Pipeline(self).run_fixture(
            "tc28_catalog", rules=os.path.join(ROOT, "frecon", "data", "RV-SIM-2026-11-12-A.catalog.json"))
        self._blocked(run, "VAL-002", "UNFROZEN")

    def test_TC_28_REC_002_requested_snapshot_mismatch(self):
        run = Pipeline(self).run_fixture("tc28_catalog", version="RV-SIM-2026-11-12-B")
        self._blocked(run, "REC-002")
        run2 = Pipeline(self).run_fixture("tc28_catalog", sha="0" * 64)
        self._blocked(run2, "REC-002")


class TC29Period(unittest.TestCase):
    def test_TC_29_unrecognised_batch_period(self):
        run = Pipeline(self).run_fixture("tc29_period")
        self.assertEqual(run["exit"], C.EXIT_BATCH_BLOCKED)
        res = run["results"]
        self.assertEqual([r["code"] for r in res["batch_block_reasons"]], ["VAL-003"])
        q01 = item(res, "Q01")
        self.assertEqual((q01["billing_period"], q01["block_scope"], q01["output_amount"]),
                         ("UNK-PERIOD", C.SCOPE_BATCH, None))
        self.assertNotIn(C.ST_PASS, res["summary"]["by_biz_status"])


if __name__ == "__main__":
    unittest.main()
