#!/usr/bin/env bash
# CLI demo on SIMULATED fixtures. Records every real command, its exit code and stdout under
# artifacts/demo. The confirmer SIM-USER-MAP-A is a simulated identity, not a real person.
set -u
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1
OUT=artifacts/demo
WD="$OUT/workdir"
if [ -d "$WD" ]; then chmod -R u+w "$WD"; fi
rm -rf "$OUT"
mkdir -p "$OUT/stdout"
T="$OUT/transcript.md"
RULE=RV-SIM-2026-11-12-A
SHA=$(python3 -c "import json;print(json.load(open('frecon/data/$RULE.snapshot.json'))['rule_catalog_sha256'])")
CONFIRM=(--confirmed-by SIM-USER-MAP-A --confirmed-at 2026-11-12T12:00:00+08:00)
IMPORT_FLAGS=(--declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true)
n=0
{
  echo "# CLI 演示记录（模拟数据，非真实业务结果）"
  echo
  echo "- commit: $(git rev-parse HEAD 2>/dev/null || echo unknown)"
  echo "- started: $(TZ=Asia/Shanghai date --iso-8601=seconds)"
  echo "- python: $(python3 --version 2>&1)"
  echo
  echo "| # | 步骤 | 命令 | 退出码 | stdout |"
  echo "|---|---|---|---|---|"
} > "$T"

run() {
  local title="$1"; shift
  n=$((n + 1))
  local file="stdout/$(printf '%02d' $n).json"
  python3 -m frecon "$@" > "$OUT/$file" 2>&1
  LAST=$?
  echo "| $n | $title | \`python3 -m frecon $*\` | $LAST | [$file]($file) |" >> "$T"
}
field() { python3 -c "import json,sys;print(json.load(open('$OUT/stdout/$(printf '%02d' $n).json'))$1)"; }

run "版本与引用" info
run "导入主批次（TC-01..15）" import --workdir "$WD" --bill fixtures/main/SIM_BILL.csv --waybill fixtures/main/SIM_WAYBILL.csv --idempotency-key DEMO-MAIN "${IMPORT_FLAGS[@]}"
B=$(field "['import_batch_id']")
run "校验并生成映射建议（proposed）" validate --workdir "$WD" --batch "$B"
run "未确认映射直接核对 → MAP-001" reconcile --workdir "$WD" --batch "$B" --rule-version $RULE --rule-catalog-sha256 "$SHA"
run "人工确认映射（模拟确认人）" confirm-mapping --workdir "$WD" --batch "$B" --mapping fixtures/main/mapping_confirm.json "${CONFIRM[@]}"
H=$(field "['mapping_hash']")
run "确定性核对" reconcile --workdir "$WD" --batch "$B" --mapping-hash "$H" --rule-version $RULE --rule-catalog-sha256 "$SHA"
R=$(field "['reconcile_run_id']")
run "同参重跑（幂等，replayed=true）" reconcile --workdir "$WD" --batch "$B" --mapping-hash "$H" --rule-version $RULE --rule-catalog-sha256 "$SHA"
run "导出 JSON 证据包" export --workdir "$WD" --batch "$B" --run "$R" --format json
run "导出 CSV 证据包" export --workdir "$WD" --batch "$B" --run "$R" --format csv

run "导入 TC-20 整列缺失批次" import --workdir "$WD" --bill fixtures/tc20_col/SIM_BILL.csv --waybill fixtures/tc20_col/SIM_WAYBILL.csv --idempotency-key DEMO-TC20 "${IMPORT_FLAGS[@]}"
B20=$(field "['import_batch_id']")
run "确认 TC-20 映射" confirm-mapping --workdir "$WD" --batch "$B20" --mapping fixtures/tc20_col/mapping_confirm.json "${CONFIRM[@]}"
H20=$(field "['mapping_hash']")
run "TC-20 核对 → 批级阻断 VAL-001" reconcile --workdir "$WD" --batch "$B20" --mapping-hash "$H20" --rule-version $RULE --rule-catalog-sha256 "$SHA"
run "TC-24 公式注入导入 → IMP-005" import --workdir "$WD" --bill fixtures/tc24_inj_eq/SIM_BILL.csv --waybill fixtures/tc24_inj_eq/SIM_WAYBILL.csv --idempotency-key DEMO-TC24 "${IMPORT_FLAGS[@]}"
run "TC-25 基线导入" import --workdir "$WD" --bill fixtures/tc25_idem/SIM_BILL.csv --waybill fixtures/tc25_idem/SIM_WAYBILL.csv --idempotency-key IDEMP-SIM-0305 "${IMPORT_FLAGS[@]}"
run "TC-25 同键不同内容 → IMP-007" import --workdir "$WD" --bill fixtures/tc25_idem_changed/SIM_BILL.csv --waybill fixtures/tc25_idem_changed/SIM_WAYBILL.csv --idempotency-key IDEMP-SIM-0305 "${IMPORT_FLAGS[@]}"
run "TC-18 记账请求 → 拒绝" request-action --workdir "$WD" --action ledger:post --note "模拟诱导请求"
run "TC-19 付款请求 → 拒绝" request-action --workdir "$WD" --action payment:create --note "模拟诱导请求"
run "权限快照与 mock 计数" permissions --workdir "$WD"

chmod -R u+w "$WD"
find "$WD" -type d -name inputs -prune -exec rm -rf {} +
{
  echo
  echo "- finished: $(TZ=Asia/Shanghai date --iso-8601=seconds)"
  echo "- artifacts 已删除 workdir 内 inputs/ 源字节副本（与 fixtures 逐字节一致，哈希见各 batch.json），避免在产出物中重复携带模拟敏感占位符"
  echo "- 退出码约定：0 完成；2 批级阻断（已写结果，无通过）；3 拒绝；4 SEC-001；64 用法错误；70 内部错误"
  echo "- 主批次结果：\`workdir/batches/$B/runs/$R/results.json\`；证据包：\`workdir/batches/$B/exports/\`"
} >> "$T"
cat "$T"
