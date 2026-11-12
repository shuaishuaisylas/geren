"""Self-built unit and boundary tests (decimal, hashing, guards, CLI contract). Not baseline cases."""

import glob
import json
import os
import re
import unittest
from decimal import Decimal

from helpers import C, ROOT, RULE_SHA, Pipeline, sensitive_tokens
from frecon.ingest import formula_prefix
from frecon.rules import FORMULAS, apply_formula
from frecon.util import ReconError, SensitiveGuard, contains_float, parse_decimal, round_half_up, sha256_file


class DecimalUnits(unittest.TestCase):
    def test_UNIT_parse_decimal_rejects_non_plain_strings(self):
        for bad in ("1e3", "NaN", "Infinity", "1,000", "1.0.0", " 1", "+1", "0x10", "", "1.", ".5"):
            self.assertIsNone(parse_decimal(bad), bad)
        self.assertEqual(parse_decimal("-0.01"), Decimal("-0.01"))
        self.assertIsNone(parse_decimal("-1", allow_negative=False))
        self.assertIsNone(parse_decimal("1.005", max_scale=2))

    def test_UNIT_half_up_and_exact_products(self):
        self.assertEqual(str(round_half_up(Decimal("1.00500"), 2)), "1.01")
        self.assertEqual(str(round_half_up(Decimal("11.0256300"), 2)), "11.03")
        self.assertEqual(str(round_half_up(Decimal("15.68750"), 2)), "15.69")
        inter, raw = apply_formula("(3)", {"P": Decimal("3.33"), "W": Decimal("3.010"), "R": Decimal("0.10")})
        self.assertEqual((str(inter["P×W"]), str(raw)), ("10.02330", "11.0256300"))
        self.assertEqual(sorted(FORMULAS), ["(1)", "(2)", "(3)"])

    def test_UNIT_results_contain_no_binary_floats(self):
        pipe = Pipeline(self)
        run = pipe.run_fixture("main")
        self.assertFalse(contains_float(run["results"]))
        amounts = [i[f] for i in run["results"]["items"] for f in ("output_amount", "diff_amount", "bill_amount")]
        self.assertTrue(all(a is None or isinstance(a, str) for a in amounts))


class GuardUnits(unittest.TestCase):
    def test_UNIT_sensitive_guard_blocks_leak(self):
        tokens = sensitive_tokens("main")
        guard = SensitiveGuard(tokens[:1])
        with self.assertRaises(ReconError) as ctx:
            guard.check("prefix " + tokens[1] + " suffix", "unit")
        self.assertEqual(ctx.exception.code, "SEC-001")
        self.assertEqual(guard.check("WB-SIM-0012 50.00", "unit"), "WB-SIM-0012 50.00")

    def test_UNIT_formula_prefix_boundary(self):
        self.assertIsNone(formula_prefix("-0.01"))
        self.assertIsNone(formula_prefix("-12"))
        self.assertEqual(formula_prefix("-1-1"), "-")
        self.assertEqual(formula_prefix("\t=1"), "TAB")
        self.assertIsNone(formula_prefix(" WB-SIM-0102"))

    def test_UNIT_engine_has_no_network_or_external_service_imports(self):
        for path in glob.glob(os.path.join(ROOT, "frecon", "*.py")):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for pattern in (r"^\s*(import|from)\s+(socket|urllib|http|requests|ssl|smtplib|ftplib)\b",):
                self.assertIsNone(re.search(pattern, text, re.M), path)


class CliContract(unittest.TestCase):
    def test_UNIT_info_and_usage_exit_codes(self):
        pipe = Pipeline(self)
        code, info = pipe.cli("info")
        self.assertEqual((code, info["sim_rule_version"], info["eval_baseline"]),
                         (0, "RV-SIM-2026-11-12-A", "EVAL-TD-BASELINE-2026-11-12-R1"))
        code, _ = pipe.cli("reconcile", "--workdir", pipe.workdir)
        self.assertEqual(code, C.EXIT_USAGE)

    def test_UNIT_desensitized_declaration_required(self):
        pipe = Pipeline(self)
        code, out = pipe.import_fixture("main", desensitized="false")
        self.assertEqual((code, out["error"]["code"]), (C.EXIT_REFUSED, "IMP-008"))

    def test_UNIT_frozen_snapshot_matches_catalog_bytes(self):
        cat = os.path.join(ROOT, "frecon", "data", "RV-SIM-2026-11-12-A.catalog.json")
        self.assertEqual(sha256_file(cat), RULE_SHA)
        with open(cat, encoding="utf-8") as fh:
            catalog = json.load(fh)
        self.assertEqual({r["approval_status"] for r in catalog["rules"]}, {"模拟约定"})
        self.assertNotIn("XX999", {r["charge_code"] for r in catalog["rules"]})

    def test_UNIT_fixture_manifest_hashes(self):
        with open(os.path.join(ROOT, "fixtures", "MANIFEST.json"), encoding="utf-8") as fh:
            manifest = json.load(fh)
        for rel, digest in manifest["files"].items():
            self.assertEqual(sha256_file(os.path.join(ROOT, "fixtures", rel)), digest, rel)

    def test_UNIT_log_is_whitelisted(self):
        pipe = Pipeline(self)
        pipe.run_fixture("main")
        allowed = {"ts", "event", "import_batch_id", "rejection_id", "source_table", "source_row_id", "code",
                   "codes", "exit_code", "ingest_status", "input_bundle_sha256", "rule_catalog_sha256",
                   "rule_snapshot_id", "rule_version", "mapping_id", "mapping_hash", "reconcile_run_id",
                   "results_sha256", "export_id", "evidence_pack_sha256", "replayed", "batch_status",
                   "row_count", "action", "decision", "idempotency_key"}
        with open(os.path.join(pipe.workdir, "logs", "recon.log.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                self.assertLessEqual(set(json.loads(line)), allowed)


if __name__ == "__main__":
    unittest.main()
