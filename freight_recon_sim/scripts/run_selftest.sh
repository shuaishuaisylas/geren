#!/usr/bin/env bash
# Runs all R&D self-tests and saves the console log, report and case matrix to artifacts/selftest.
set -u
cd "$(dirname "$0")/.."
OUT=artifacts/selftest
mkdir -p "$OUT"
export FRECON_GIT_COMMIT="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
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
