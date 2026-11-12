import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "fixtures")
sys.path.insert(0, ROOT)

from frecon import conventions as C  # noqa: E402
from frecon.cli import main as cli_main  # noqa: E402

RULE_VERSION = C.SIM_RULE_VERSION
with open(os.path.join(ROOT, "frecon", "data", f"{RULE_VERSION}.snapshot.json"), encoding="utf-8") as _fh:
    RULE_SHA = json.load(_fh)["rule_catalog_sha256"]
SIM_CONFIRMER = "SIM-USER-MAP-A"
SIM_CONFIRMED_AT = "2026-11-12T12:00:00+08:00"


def fx(*parts):
    return os.path.join(FIX, *parts)


class Pipeline:
    """Drives the real CLI (subprocess by default, in-process when patching is needed)."""

    def __init__(self, testcase, inproc=False):
        self.workdir = tempfile.mkdtemp(prefix="frecon-test-")
        testcase.addCleanup(self._cleanup)
        self.inproc = inproc
        self.stdout_log = []

    def _cleanup(self):
        for dirpath, dirnames, filenames in os.walk(self.workdir):
            for name in dirnames + filenames:
                os.chmod(os.path.join(dirpath, name), 0o755)
        shutil.rmtree(self.workdir, ignore_errors=True)

    def cli(self, *args):
        argv = [str(a) for a in args]
        if self.inproc:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = cli_main(argv)
            out = buf.getvalue()
        else:
            proc = subprocess.run([sys.executable, "-m", "frecon", *argv], cwd=ROOT, capture_output=True,
                                  text=True, timeout=120)
            code, out = proc.returncode, proc.stdout
            if proc.stderr:
                self.stdout_log.append(proc.stderr)
        self.stdout_log.append(out)
        return code, (json.loads(out) if out.strip() else None)

    def import_(self, bill, waybill, key, rules=None, snapshot=None, encoding="UTF-8", delimiter="comma",
                desensitized="true"):
        args = ["import", "--workdir", self.workdir, "--bill", bill, "--waybill", waybill,
                "--idempotency-key", key, "--declared-encoding", encoding, "--declared-delimiter", delimiter,
                "--source-declared-desensitized", desensitized]
        if rules:
            args += ["--rules", rules]
        if snapshot:
            args += ["--snapshot", snapshot]
        return self.cli(*args)

    def import_fixture(self, name, key=None, **kw):
        return self.import_(fx(name, "SIM_BILL.csv"), fx(name, "SIM_WAYBILL.csv"), key or f"IDEMP-{name}", **kw)

    def validate(self, batch, ai_proposal=None):
        args = ["validate", "--workdir", self.workdir, "--batch", batch]
        if ai_proposal:
            args += ["--ai-proposal", ai_proposal]
        return self.cli(*args)

    def confirm(self, batch, mapping_path, by=SIM_CONFIRMER, at=SIM_CONFIRMED_AT):
        return self.cli("confirm-mapping", "--workdir", self.workdir, "--batch", batch, "--mapping", mapping_path,
                        "--confirmed-by", by, "--confirmed-at", at)

    def reconcile(self, batch, mapping_hash, version=RULE_VERSION, sha=RULE_SHA):
        return self.cli("reconcile", "--workdir", self.workdir, "--batch", batch, "--mapping-hash", mapping_hash,
                        "--rule-version", version, "--rule-catalog-sha256", sha)

    def export(self, batch, run, fmt):
        return self.cli("export", "--workdir", self.workdir, "--batch", batch, "--run", run, "--format", fmt)

    def results(self, rec_payload):
        with open(os.path.join(self.workdir, rec_payload["results_path"]), encoding="utf-8") as fh:
            return json.load(fh)

    def run_fixture(self, name, mapping=None, key=None, version=RULE_VERSION, sha=RULE_SHA, **import_kw):
        code, imp = self.import_fixture(name, key=key, **import_kw)
        assert code == 0, imp
        batch = imp["import_batch_id"]
        self.validate(batch)
        code, conf = self.confirm(batch, mapping or fx(name, "mapping_confirm.json"))
        assert code == 0, conf
        rc, rec = self.reconcile(batch, conf["mapping_hash"], version=version, sha=sha)
        return {"batch": batch, "import": imp, "mapping": conf, "exit": rc, "rec": rec,
                "results": self.results(rec) if rec and "results_path" in rec else None}

    def all_output_text(self):
        """Every observable output: CLI stdout/stderr plus every file the tool wrote."""
        chunks = list(self.stdout_log)
        for dirpath, _dirs, files in os.walk(self.workdir):
            for name in files:
                path = os.path.join(dirpath, name)
                if os.sep + "inputs" + os.sep in path:
                    continue
                with open(path, "rb") as fh:
                    chunks.append(fh.read().decode("utf-8", errors="replace"))
        return "\n".join(chunks)


def item(results, source_row_id, table=None):
    found = [i for i in results["items"] if i["source_row_id"] == source_row_id
             and (table is None or i["source_table"] == table)]
    assert len(found) == 1, f"expected one item for {source_row_id}, got {len(found)}"
    return found[0]


def codes(it):
    return {r["code"] for r in it["block_reason"]}


def missing_evidence(it):
    return [f for f in C.EVIDENCE_REQUIRED_FIELDS if f not in it]


def sensitive_tokens(fixture_name):
    tokens = []
    for table in ("SIM_BILL", "SIM_WAYBILL"):
        with open(fx(fixture_name, f"{table}.csv"), encoding="utf-8") as fh:
            import csv

            for row in csv.DictReader(fh):
                remark = row.get("remark") or ""
                if remark:
                    tokens.append(remark)
                    tokens.extend(t for t in remark.split() if len(t) >= 6)
    return tokens
