#!/usr/bin/env bash
# Runs all R&D self-tests; writes console log, report and case matrix to artifacts/runs/<commit>/selftest
# (never overwrites an earlier run; the b139d97 run is kept at artifacts/selftest).
set -u
cd "$(dirname "$0")/.."
REV="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
if [ -n "$(git status --porcelain -- . ':!artifacts' 2>/dev/null)" ]; then REV="${REV}-dirty"; fi
OUT="artifacts/runs/${REV}/selftest"
if [ -e "$OUT" ]; then echo "refusing to overwrite existing $OUT" >&2; exit 1; fi
mkdir -p "$OUT"
export FRECON_GIT_COMMIT="$REV"
export PYTHONDONTWRITEBYTECODE=1
{
  echo "# command: python3 tests/run_selftest.py --out $OUT"
  echo "# commit under test: $FRECON_GIT_COMMIT"
  echo "# started: $(TZ=Asia/Shanghai date --iso-8601=seconds)"
} > "$OUT/selftest_console.log"
python3 tests/run_selftest.py --out "$OUT" >> "$OUT/selftest_console.log" 2>&1
code=$?
echo "# exit_code: $code" >> "$OUT/selftest_console.log"
tail -n 8 "$OUT/selftest_console.log"
exit $code
