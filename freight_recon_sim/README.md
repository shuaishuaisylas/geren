# 运费账单对账离线原型（模拟）· freight_recon_sim

> **声明**：本目录全部数据、规则、金额与结论均为**模拟**，不是德邦股份/德邦快递的业务规则、生产数据、计费结果或已验证效果。
> 研发自测通过 ≠ 独立评测通过 ≠ 业务验收。独立评测（EVAL-TD-BASELINE-2026-11-12-R1）状态：**未执行**。
> 不接生产、不记账、不付款、不改写源文件、不连接任何外部服务（含 AI）。不包含任何处理量、耗时或收益数字。

## 1. 版本

| 项 | 值 |
|---|---|
| 原型引擎 `frecon` | `0.1.0`（交付修订 r2 未改引擎逻辑；结果哈希与 b139d97 一致，见第 13 节） |
| 交付修订 | r1 = 提交 `b139d97f791a723f253bbe000c177c0236854971`；r2 = PRD-CHG-2026-11-12-A 对齐（测试分类、补充用例、文档、脚本），被测提交见 `artifacts/runs/` |
| 工程模拟约定 | `ENG-SIM-2026-11-12-A`（表头别名字典 `ENG-SIM-ALIAS-2026-11-12-A`） |
| 冻结模拟规则 | `RV-SIM-2026-11-12-A`，快照 `SNAP-SIM-2026-11-12-A-001`，目录 SHA-256 `d2a45c286a811211840c9d8f64baddf908ccdb76de6f8265cf6c76b73000cb43` |
| 评测基线 | `EVAL-TD-BASELINE-2026-11-12-R1`（29 条，TC-01–TC-29，取代此前 15/21 条快照） |
| 需求依据 | 业务模板 `TPL-BIZ-FIELD-RULE-2026-11-12-A` + PRD `PRD-FREIGHT-RECON-2026-11-12-A` |
| 设计建议 | 研发设计 `TPL-ENG-RECON-DESIGN-2026-11-12-A`（R2） |
| 接口约定 | `recon.v1-design`，以离线 CLI 落地，**不是**已部署服务 |

## 2. 环境要求

- Python ≥ 3.10（实测 3.12.3），**仅标准库**，无第三方依赖，完全离线。
- `scripts/*.sh` 需要 bash 与 git（仅用于记录被测提交号）。
- 不需要网络、数据库、凭据。

## 3. 安装与启动

无需安装。在本目录执行：

```bash
cd freight_recon_sim
python3 -m frecon info                 # 版本与引用
python3 -m frecon --help               # 全部子命令
bash scripts/demo.sh                   # 端到端 CLI 演示，输出到 artifacts/runs/<commit>/demo/（不覆盖旧记录）
```

### 3.1 最小只读流程（与 R2 接口一一对应）

```bash
WD=/tmp/frecon-wd
RULE=RV-SIM-2026-11-12-A
SHA=d2a45c286a811211840c9d8f64baddf908ccdb76de6f8265cf6c76b73000cb43

# 1) 导入：冻结源哈希、规则快照；拒绝公式注入/编码/格式问题
python3 -m frecon import --workdir $WD --bill fixtures/main/SIM_BILL.csv --waybill fixtures/main/SIM_WAYBILL.csv \
  --idempotency-key DEMO-MAIN --declared-encoding UTF-8 --declared-delimiter comma --source-declared-desensitized true
# 2) 校验：只产生映射建议（proposed），可附离线 AI 候选（--ai-proposal），永不自动确认
python3 -m frecon validate --workdir $WD --batch <import_batch_id>
# 3) 人工确认映射（确认人不得为 AI/系统；时间必须带 +08:00）。SIM- 前缀为模拟确认人
python3 -m frecon confirm-mapping --workdir $WD --batch <id> --mapping fixtures/main/mapping_confirm.json \
  --confirmed-by SIM-USER-MAP-A --confirmed-at 2026-11-12T12:00:00+08:00
# 4) 核对：必须带已确认 mapping_hash 与冻结规则版本/哈希
python3 -m frecon reconcile --workdir $WD --batch <id> --mapping-hash <mapping_hash> --rule-version $RULE --rule-catalog-sha256 $SHA
# 5) 导出脱敏证据包（json / csv）
python3 -m frecon export --workdir $WD --batch <id> --run <reconcile_run_id> --format json
# 超范围请求（记账/付款/改写源）一律拒绝，仅写隔离 mock 日志
python3 -m frecon request-action --workdir $WD --action ledger:post
python3 -m frecon permissions --workdir $WD
```

退出码：`0` 完成；`2` 批级阻断（结果已写出，无任何“核对通过候选”）；`3` 拒绝（导入被拒、MAP-001/002/003、IMP-007、超范围请求、EXP-001）；`4` SEC-001 数据保护拒绝；`64` 用法错误；`70` 内部错误。每个命令只向 stdout 输出一个 JSON。

## 4. 测试命令

```bash
bash scripts/run_selftest.sh           # 全量自测，写 artifacts/runs/<commit>/selftest/（目录已存在则拒绝覆盖）
python3 tests/run_selftest.py --out /tmp/selftest   # 同上，不写入仓库
cd tests && python3 -m unittest -v     # 纯 unittest
python3 scripts/build_fixtures.py      # 重新生成夹具与快照（确定性；MANIFEST.json 记录哈希）
```

测试分三类，文件与命名严格区分：

| 文件 | 类别 | 说明 |
|---|---|---|
| `tests/test_eval_baseline_tc01_15.py` | 基线用例研发自测 | 原 TC-01–TC-15，保留原编号 |
| `tests/test_eval_baseline_tc16_29.py` | 基线用例研发自测 | TC-16–TC-29，保留原编号；多阶段用例拆为 `_A/_B/_C` |
| `tests/test_selfbuilt_tc_d01_d08.py` | **自建补测** | R2 设计 TC-D01–TC-D08，非基线、不计入独立评测 |
| `tests/test_selfbuilt_prdchg_supplement.py` | **补充用例** | 按 PRD-CHG-2026-11-12-A 拆分单列：TC-16-S1、TC-20-S1、TC-21-S1；非冻结基线，是否纳入评测由评测负责人登记 |
| `tests/test_selfbuilt_units.py` | 自建单元/边界 | 十进制解析、舍入、守卫、日志白名单、夹具哈希 |

用例 ↔ 测试映射、每条的自测结果与独立评测状态见各次运行的 `selftest_matrix.md`（索引见 `artifacts/README.md`）。基线用例全部通过 CLI（子进程或进程内调用同一 `main`）驱动，评测方可用相同夹具与命令独立复测。

## 5. 模拟数据（`fixtures/`，全部 SIMULATED）

| 目录 | 对应用例 | 说明 |
|---|---|---|
| `main/` | TC-01–TC-15 | B01–B16 / W01–W16 逐条取自基线源表；TC-12 模拟占位符放在 `remark` 列 |
| `tc16_map/` | TC-16 | 中文表头按基线原文；含 AI 候选 `MAP-CAND-SIM-01` 与人工确认文件 |
| `tc16_map_supplement/` | TC-16-S1（补充用例） | 账单表头补“币种”列，**仅验证可消除 VAL-001 缺列障碍，不代表核对成功**（DEC-SIM-C3-TC16） |
| `tc17_ro/` | TC-17 | 运单号左侧空格，规范化仅在派生副本 |
| `tc18_ledger/`, `tc19_pay/` | TC-18/19 | remark 含模拟诱导语（不被解释、不输出） |
| `tc20_col/`, `tc20_col_fixed/` | TC-20 | 整列缺 `chargeable_weight` / 补列后重确认重跑 |
| `tc21_dep_a/`, `tc21_dep_b/` | TC-21 | FS001 依赖 FR001 两阶段 |
| `tc22_delim/` | TC-22 | 字段含 `|` |
| `tc23_formula_a/b/` + `rules/RV-SIM-NEG-TC23A/B.*` | TC-23 | 反例目录（仅测试用，单独版本号） |
| `tc24_inj_{eq,plus,at,minus}/`, `tc24_negative/` | TC-24 | `=`/`+`/`@`/非数值`-` 注入与 `-0.01` 对照 |
| `tc25_idem/`, `tc25_idem_changed/` | TC-25 | 幂等键 `IDEMP-SIM-0305` |
| `tc26_multi/` | TC-26 | 账单侧重复 + 运单多候选 |
| `tc27_enc/`, `tc27_fmt/` | TC-27 | GBK 字节 / 未闭合引号 |
| `tc28_catalog/` + `rules/malformed.catalog.json` | TC-28 | LOAD / UNFROZEN / REC-002 三触发 |
| `tc29_period/` | TC-29 | 账期 `UNK-PERIOD` |

内置冻结规则：`frecon/data/RV-SIM-2026-11-12-A.{catalog,snapshot}.json`（FR001/FS001/SX001，审批状态“模拟约定”；XX999 故意未登记）。

## 6. 状态与证据说明

- `biz_status` 只取业务模板四值：`核对通过候选` / `账单不通过，须复核` / `漏计候选，须复核` / `待人工确认`。“核对通过候选”**不等于**验收通过。
- 批级：`batch_status` 为 `整批阻断，不得输出通过` 时，所有行 `block_scope=批级`、`biz_status=待人工确认`、金额置空，不构键、不计算，但保留源行与原因。
- 行级优先级（PRD ②–⑨ / R2 表 5–11）：VAL-014 键不完整 → REC-010 运单多候选 → REC-011 账单重复 → REC-012/013 单侧缺失 → 规则/输入/币种类（CAL-001/002/003/004/006、RUL-001/002、VAL-010–013）→ CAL-005 超容差 → 通过候选。**全部命中原因**都写入 `block_reason`，状态取最高优先级。
- 金额：只用 `decimal.Decimal` 精确乘加（精度损失即报错），仅对行最终值 `ROUND_HALF_UP` 到 2 位；所有金额与中间值以**十进制字符串**输出；拒绝科学计数、NaN、千分位、浮点。无规则/缺输入/多候选时 `output_amount`、`diff_amount` 为 `null`，不用账单额顶替或反推。
- 证据链最低字段（业务模板）全部输出：`source_table, source_row_id, candidate_row_ids, import_batch_id, match_key, charge_code, billing_period, rule_version, formula_id, inputs, intermediates, rounding_mode/scale/layer, output_amount, diff_amount, currency, biz_status, block_scope, block_reason, completeness_flag, human_review_record`；另有 `source_row_sha256`、`base_amount_source`、多候选各自计算过程 `candidates[]`。
- `human_review_record` 固定为 `null`（预留，本阶段为空，不虚构）；`ai_explanation=null`、`ai_explanation_status=skipped`。
- 可追溯：`input_bundle_sha256`、逐文件与逐行 SHA-256、规则快照哈希、`mapping_hash`、确定性 `reconcile_run_id` / `results_sha256` / `evidence_pack_sha256`；同参重跑返回原结果（`replayed=true`），不覆盖。

## 7. 权限与安全边界

- 源文件只读：导入时复制为只读副本（0444），规范化只写派生副本（含血统与 transform_log）；跑次前后比对源哈希（SRC-001）。
- 无记账/付款/改写源执行路径；`request-action` 统一 `OOS-001` 拒绝，写本地隔离 mock（`SIM-MOCK-LEDGER/PAY/SOURCE-WRITE`），成功计数恒为 0。
- 无网络：引擎不 import 任何网络库；测试中打桩 socket 验证 0 次连接。
- 不求值：公式为闭集 (1)(2)(3)，`formula_expr` 只做文本比对；引擎不含 `eval/exec/compile`（静态检查 + 运行时打桩计数 = 0）。
- 输出白名单：结果/导出/日志只写白名单字段；`remark`、`contract_id` 视为敏感，不进入结果、派生副本、导出、日志；写出前做原文扫描，命中即 SEC-001 拒绝写出。
- CSV：导入拒绝 `=`、`+`、`@`、Tab/CR 与非数值 `-` 前缀（IMP-005，整批）；导出对上述前缀加 `'` 中和，纯负数（如 `-0.01`）保留。
- 映射门禁：未人工确认、hash 不符或确认后内容被改 → MAP-001；AI/系统身份确认 → MAP-002。
- 原型不做身份鉴权，`confirmed_by` 由调用方声明；真实权限/角色待确认。

## 8. 未确认的八项真实业务口径（全部“待业务确认”，本原型只用模拟约定）

1. 收费项唯一键组成与重复计费归类（模拟：`waybill_no|charge_code`）。
2. 税费（含税/价税分离/税码税率来源）与币种、汇率（模拟：仅 CNY、不含税、无税率）。
3. 舍入层级、方式与容差（模拟：行最终值 ROUND_HALF_UP 2 位，容差 0.00 绝对额）。
4. 附加费基数（模拟：同运单 FR001 已舍入行金额）。
5. 缺字段阻断范围与是否允许替代字段（模拟：不允许替代）。
6. 缺失单据的账期窗口与延迟入账/跨期（模拟：仅本批，不跨期）。
7. 匹配不唯一时的人工确认流程与是否允许规则消歧（模拟：不消歧）。
8. 敏感字段清单与脱敏规则（含审计内档）（模拟：remark/contract_id 不输出）。

另：真实脱敏样本、业务金标、人工基线与验收阈值均未提供，真实业务效果验收不得启动。

## 9. 需求冲突与差异报告（显式列出，不替业务裁定）

| # | 冲突/缺口 | 来源 | 本原型处理 |
|---|---|---|---|
| C1 | R2 `IMP-004` 含“缺少白名单必填列”（导入即拒）vs 业务模板/PRD/R2 `VAL-001`/基线 TC-20（批级阻断但保留已解析行） | R2 内部 | 已由产品裁决 DEC-SIM-C1-VAL001 确认：必填列整列缺失记 VAL-001；IMP-004 只用于未登记表头 |
| C2 | R2“未登记列拒绝整批”vs 基线 TC-12/18/19/24 在源行注入备注类文本；业务模板字段字典无备注字段 | 模板 vs 基线 | ENG-SIM 增登记敏感字段 `remark`（不参与计算、不输出） |
| C3 | 基线 TC-16 账单表头无“币种”vs 模板“currency 模拟必填” | 基线 vs 模板 | 已由产品裁决 DEC-SIM-C3-TC16：原夹具确认映射后仍 VAL-001 批阻断；补充用例 TC-16-S1（加“币种”）仅消除缺列障碍，不表述为核对成功 |
| C4 | 基线 TC-16 未给 MW01 重量值；TC-20 修复后、TC-29 运单 W 值未给 | 基线 | ENG-SIM 夹具假设（10.000 等），在 MANIFEST.json 标注 |
| C5 | R2 `IMP-005` “行或批”；基线 TC-24 主状态为整批阻断 | R2 vs 基线 | 取整批拒绝（更严格，基线允许） |
| C6 | TC-21 阶段 B：R2 只写“FS001 不得自动判通过候选”，未定状态与代码 | R2/基线 | ENG-SIM：`待人工确认` + 新码 `CAL-006`；产品裁决 DEC-SIM-C6-CAL006 认可为模拟原型暂定安全状态，非业务定论 |
| C7 | 模板 `completeness_flag` 枚举无批级阻断值 | 模板 | ENG-SIM 值 `未评估（批级阻断）` |
| C8 | 基线状态“应拒绝，无副作用”“整批阻断，不得输出通过”不属模板四值 | 基线 vs 模板 | 作为批级/拒绝结果（`batch_status`、退出码、拒绝记录），行级仍只用四值 |
| C9 | TC-17 行哈希算法与派生副本格式未定；基线 `simsha256:*` 为占位 | 基线 | 文件字节 SHA-256；行哈希=原始单元格列表规范 JSON 的 SHA-256；派生副本 `DRV-<batch>-<table>` v1 |
| C10 | “公式态短横”与负金额边界未定 | R2/基线 | ENG-SIM：`-` 开头且整格为十进制数则允许，否则拒绝；另拒 Tab/CR 前缀、忽略前导空格 |
| C11 | 批次账期定义未给 | 模板/基线 | ENG-SIM：账单行中可识别（`SIM-YYYY-MM`）账期恰为 1 个，否则 VAL-003；个别行不可识别为行级 VAL-010 |
| C12 | 目录内容与快照哈希不符的归类 | R2 | 记 VAL-002（UNFROZEN）；REC-002 仅用于请求参数与快照不符 |
| C13 | R2 允许缺省规则目录 | R2 | 缺省使用内置冻结 `RV-SIM-2026-11-12-A`；显式给出但不可读/无快照则批级阻断 |
| C14 | 项目经理口径“21 例”vs 附件实际：先前评测快照正文已有 TC-01–TC-25，最新冻结基线为 29 例 | 沟通 vs 附件 | 以最新附件 `EVAL-TD-BASELINE-2026-11-12-R1` 的 29 例为准 |
| C15 | 运单侧计算输入列何时“整列必填” | 模板/R2 | ENG-SIM：仅本批出现的收费项所命中规则的输入列为整列必填；单格为空为行级 CAL-002 |

## 10. ENG-SIM-2026-11-12-A 工程模拟约定（与冻结规范的差异）

均为原型工程选择，非业务口径；业务确认后须升版本：

- 新增登记字段 `remark`（敏感，C2）；`contract_id` 按敏感候选处理。
- 表头别名字典 `ENG-SIM-ALIAS-2026-11-12-A`，仅生成建议；`重量` 判为歧义（计费重/实际重）。
- 新增代码：`MAP-003`（确认内容不合法）、`SRC-001`（源副本哈希变化）、`OOS-001`（超范围请求）、`CAL-006`（依附基础行未通过）。
- `bill_amount` 至多 2 位小数；P/W/R 须为非负十进制。
- 设有有效期的规则在原型中不自动命中（CAL-001），因账期对齐依据未确认。
- 目录 `approval_status` 必须以“模拟约定”开头，否则整目录不加载（VAL-002）。
- 导入上限：单文件 5 MiB、10 万行（IMP-006）。
- 其余见第 9 节 C5–C15。

## 11. 未执行 / 不在首版 / 变更清单

- **未执行**：基线 29 例的**独立评测**（由评测方执行）；真实业务效果验收；安全测评。
- **不在首版（仅列变更清单，不扩首版）**：XLSX 导入（M-XLS）、正式 AI 差异说明（M-AI，仅留 `ai_boundary.py` 边界）、HTTP 服务与鉴权、UI、多币种/税口径、跨期窗口、消歧规则、审计内档脱敏策略、生产接入。
- 首版范围以 29 例为限；后续新增需求须登记变更（日期、提出方、摘要、影响用例、是否需新基线版本）后升版纳入。

## 12. 产出物（`artifacts/`，模拟、已脱敏）

索引见 `artifacts/README.md`。旧记录不覆盖、不改写：

- `artifacts/selftest/`、`artifacts/demo/`：r1，被测提交 `b139d97`（67 个测试）。其中 TC-16 计 4 个测试，含补币种夹具测试；该分类已按 DEC-SIM-C3-TC16 在 r2 改为补充用例单列，旧日志原样保留。
- `artifacts/runs/<commit>/{selftest,demo}/`：r2 及以后各次运行，按被测提交完整 SHA 分目录。

每个运行目录包含 `selftest_console.log`（真实命令、逐测试输出、退出码）、`selftest_report.json`、`selftest_matrix.md`，以及演示 `transcript.md`、`stdout/*.json`、`workdir/`（已删除 `inputs/` 源字节副本）。

## 13. PRD-CHG-2026-11-12-A 产品三项裁决核查（研发自测，非独立评测）

| 裁决 | 现状（r1 / b139d97 实现） | 研发自测证据 | r2 处理 |
|---|---|---|---|
| DEC-SIM-C3-TC16 | 原夹具：未确认即 MAP-001；确认后 VAL-001（缺 `SIM_BILL.currency`），整批阻断、退出码 2、无通过导出。符合 | `test_TC_16_A_*`、`test_TC_16_B_*`（r2 增断言：全部行批级、金额空、导出通过数 0） | **缺陷（测试分类与表述，非引擎）**：r1 把补币种夹具测试计入 TC-16，README 表述为“证明确认后可进入核对”。r2 拆为补充用例 TC-16-S1，只断言 VAL-001 消除、映射已绑定，不断言核对成功 |
| DEC-SIM-C1-VAL001 | 整列缺失：导入 accepted，核对 VAL-001 批阻断，6 行全部保留源行哈希与原因，金额空，无通过。IMP-004 仅在 `parse_source` 中用于未登记表头。符合 | `test_TC_20_*`（r2 增断言：导入 accepted、每行仅 VAL-001、行哈希保留）；新 `test_TC_20_S1_*` | **测试缺口**：r1 无 IMP-004 专项测试。r2 新增 TC-20-S1：未登记表头→IMP-004 且不建批次；仅缺必填列→不触发 IMP-004 |
| DEC-SIM-C6-CAL006 | 阶段 A：FS001 CAL-003、`output_amount=null`。阶段 B：FR001 保持“账单不通过”(CAL-005, 差 6.50)，FS001 保留可算证据（基数 125.50、中间值 15.68750、输出 15.69），状态“待人工确认”(CAL-006)，非通过候选。符合 | `test_TC_21_A_*`、`test_TC_21_B_*`（r2 增断言：G01 仅 CAL-005 未被覆盖，G02 仅 CAL-006、证据字段齐全）；新 `test_TC_21_S1_*`（附加费金额也不符时仍不通过、基础行不变） | 无代码修改；只补强断言 |

结论：三项裁决在引擎行为上均已符合，引擎代码 `frecon/` 在 r2 未改动。r2 修正了一处测试分类与表述缺陷（C3），补了一处测试缺口（C1）。

