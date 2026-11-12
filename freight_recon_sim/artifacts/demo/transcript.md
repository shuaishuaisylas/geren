# CLI 演示记录（模拟数据，非真实业务结果）

- commit: b139d97f791a723f253bbe000c177c0236854971
- started: 2026-11-12T11:58:11+08:00
- python: Python 3.12.3

| # | 步骤 | 命令 | 退出码 | stdout |
|---|---|---|---|---|
| 1 | 版本与引用 | `python3 -m frecon info` | 0 | [stdout/01.json](stdout/01.json) |
| 2 | 导入主批次（TC-01..15） | `python3 -m frecon import --workdir artifacts/demo/workdir --bill fixtures/main/SIM_BILL.csv --waybill fixtures/main/SIM_WAYBILL.csv --idempotency-key DEMO-MAIN --declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true` | 0 | [stdout/02.json](stdout/02.json) |
| 3 | 校验并生成映射建议（proposed） | `python3 -m frecon validate --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad` | 0 | [stdout/03.json](stdout/03.json) |
| 4 | 未确认映射直接核对 → MAP-001 | `python3 -m frecon reconcile --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad --rule-version RV-SIM-2026-11-12-A --rule-catalog-sha256 d2a45c286a811211840c9d8f64baddf908ccdb76de6f8265cf6c76b73000cb43` | 3 | [stdout/04.json](stdout/04.json) |
| 5 | 人工确认映射（模拟确认人） | `python3 -m frecon confirm-mapping --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad --mapping fixtures/main/mapping_confirm.json --confirmed-by SIM-USER-MAP-A --confirmed-at 2026-11-12T12:00:00+08:00` | 0 | [stdout/05.json](stdout/05.json) |
| 6 | 确定性核对 | `python3 -m frecon reconcile --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad --mapping-hash 1735e6b1131c04de5b359ac1b1ac982058222f98550d66a68fa9d5d436daa0db --rule-version RV-SIM-2026-11-12-A --rule-catalog-sha256 d2a45c286a811211840c9d8f64baddf908ccdb76de6f8265cf6c76b73000cb43` | 0 | [stdout/06.json](stdout/06.json) |
| 7 | 同参重跑（幂等，replayed=true） | `python3 -m frecon reconcile --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad --mapping-hash 1735e6b1131c04de5b359ac1b1ac982058222f98550d66a68fa9d5d436daa0db --rule-version RV-SIM-2026-11-12-A --rule-catalog-sha256 d2a45c286a811211840c9d8f64baddf908ccdb76de6f8265cf6c76b73000cb43` | 0 | [stdout/07.json](stdout/07.json) |
| 8 | 导出 JSON 证据包 | `python3 -m frecon export --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad --run RR-SIM-caaeb0affdceb56c --format json` | 0 | [stdout/08.json](stdout/08.json) |
| 9 | 导出 CSV 证据包 | `python3 -m frecon export --workdir artifacts/demo/workdir --batch IB-SIM-93b8e7e7d3e246ad --run RR-SIM-caaeb0affdceb56c --format csv` | 0 | [stdout/09.json](stdout/09.json) |
| 10 | 导入 TC-20 整列缺失批次 | `python3 -m frecon import --workdir artifacts/demo/workdir --bill fixtures/tc20_col/SIM_BILL.csv --waybill fixtures/tc20_col/SIM_WAYBILL.csv --idempotency-key DEMO-TC20 --declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true` | 0 | [stdout/10.json](stdout/10.json) |
| 11 | 确认 TC-20 映射 | `python3 -m frecon confirm-mapping --workdir artifacts/demo/workdir --batch IB-SIM-70a07d8b16797aaa --mapping fixtures/tc20_col/mapping_confirm.json --confirmed-by SIM-USER-MAP-A --confirmed-at 2026-11-12T12:00:00+08:00` | 0 | [stdout/11.json](stdout/11.json) |
| 12 | TC-20 核对 → 批级阻断 VAL-001 | `python3 -m frecon reconcile --workdir artifacts/demo/workdir --batch IB-SIM-70a07d8b16797aaa --mapping-hash b8e67a500c40b892329d908da0a33e8f6036029db7f40ce3dbea78b677f0ec24 --rule-version RV-SIM-2026-11-12-A --rule-catalog-sha256 d2a45c286a811211840c9d8f64baddf908ccdb76de6f8265cf6c76b73000cb43` | 2 | [stdout/12.json](stdout/12.json) |
| 13 | TC-24 公式注入导入 → IMP-005 | `python3 -m frecon import --workdir artifacts/demo/workdir --bill fixtures/tc24_inj_eq/SIM_BILL.csv --waybill fixtures/tc24_inj_eq/SIM_WAYBILL.csv --idempotency-key DEMO-TC24 --declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true` | 3 | [stdout/13.json](stdout/13.json) |
| 14 | TC-25 基线导入 | `python3 -m frecon import --workdir artifacts/demo/workdir --bill fixtures/tc25_idem/SIM_BILL.csv --waybill fixtures/tc25_idem/SIM_WAYBILL.csv --idempotency-key IDEMP-SIM-0305 --declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true` | 0 | [stdout/14.json](stdout/14.json) |
| 15 | TC-25 同键不同内容 → IMP-007 | `python3 -m frecon import --workdir artifacts/demo/workdir --bill fixtures/tc25_idem_changed/SIM_BILL.csv --waybill fixtures/tc25_idem_changed/SIM_WAYBILL.csv --idempotency-key IDEMP-SIM-0305 --declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true` | 3 | [stdout/15.json](stdout/15.json) |
| 16 | TC-18 记账请求 → 拒绝 | `python3 -m frecon request-action --workdir artifacts/demo/workdir --action ledger:post --note 模拟诱导请求` | 3 | [stdout/16.json](stdout/16.json) |
| 17 | TC-19 付款请求 → 拒绝 | `python3 -m frecon request-action --workdir artifacts/demo/workdir --action payment:create --note 模拟诱导请求` | 3 | [stdout/17.json](stdout/17.json) |
| 18 | 权限快照与 mock 计数 | `python3 -m frecon permissions --workdir artifacts/demo/workdir` | 0 | [stdout/18.json](stdout/18.json) |

- finished: 2026-11-12T11:58:11+08:00
- artifacts 已删除 workdir 内 inputs/ 源字节副本（与 fixtures 逐字节一致，哈希见各 batch.json），避免在产出物中重复携带模拟敏感占位符
- 退出码约定：0 完成；2 批级阻断（已写结果，无通过）；3 拒绝；4 SEC-001；64 用法错误；70 内部错误
- 主批次结果：`workdir/batches/IB-SIM-93b8e7e7d3e246ad/runs/RR-SIM-caaeb0affdceb56c/results.json`；证据包：`workdir/batches/IB-SIM-93b8e7e7d3e246ad/exports/`
