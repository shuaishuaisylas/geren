"""Frozen references and versioned engineering simulation conventions.

Everything prefixed ENG-SIM is an engineering choice made for this prototype where the
business template (TPL-BIZ-FIELD-RULE-2026-11-12-A), PRD (PRD-FREIGHT-RECON-2026-11-12-A)
and evaluation baseline (EVAL-TD-BASELINE-2026-11-12-R1) leave a gap. None of it is a
confirmed Deppon business rule. Differences are listed in README.md.
"""

from datetime import timedelta, timezone

from . import __version__

ENGINE_VERSION = __version__
ENG_SIM_VERSION = "ENG-SIM-2026-11-12-A"
API_VERSION = "recon.v1-design (offline CLI; not a deployed service)"

BIZ_TEMPLATE_ID = "TPL-BIZ-FIELD-RULE-2026-11-12-A"
PRD_ID = "PRD-FREIGHT-RECON-2026-11-12-A"
ENG_DESIGN_ID = "TPL-ENG-RECON-DESIGN-2026-11-12-A (R2)"
EVAL_BASELINE_ID = "EVAL-TD-BASELINE-2026-11-12-R1"
SIM_RULE_VERSION = "RV-SIM-2026-11-12-A"

TZ_SHANGHAI = timezone(timedelta(hours=8))

DATA_LABEL = "SIMULATED - 模拟数据与模拟规则，非德邦真实业务结果，非业务验收"
INDEPENDENT_EVAL_STATUS = f"未执行（{EVAL_BASELINE_ID} 独立评测尚未执行；研发自测不等于独立验收）"
BUSINESS_ACCEPTANCE_STATUS = "未启动（真实脱敏样本、业务金标、人工基线与阈值均未提供）"

TABLE_BILL = "SIM_BILL"
TABLE_WAYBILL = "SIM_WAYBILL"
TABLE_RULE = "SIM_RULE"
SOURCE_TABLES = (TABLE_BILL, TABLE_WAYBILL)

# biz_status: only the four values allowed by the business template.
ST_PASS = "核对通过候选"
ST_BILL_FAIL = "账单不通过，须复核"
ST_MISSED = "漏计候选，须复核"
ST_PENDING = "待人工确认"

BATCH_OK = "已完成模拟核对（结果仅为候选，须人工复核）"
BATCH_BLOCKED = "整批阻断，不得输出通过"

SCOPE_NONE = "无"
SCOPE_ROW = "行级"
SCOPE_BATCH = "批级"

CF_COMPLETE = "本批双侧完整"
CF_NO_COUNTERPART = "对侧无行"
CF_MISSING_COLUMN = "缺列"
CF_MISSING_RULE_INPUT = "缺规则输入"
CF_KEY_INCOMPLETE = "匹配键不完整"
CF_NOT_EVALUATED = "未评估（批级阻断）"  # ENG-SIM: template enum has no value for batch-blocked rows

EX_NONE = "无"
EX_DUP = "重复计费候选"
EX_MISSED = "漏计候选"
EX_AMOUNT = "金额不一致"
EX_DOC_MISSING = "单据缺失候选"
EX_OTHER = "其他"

# Match key (simulated, pending business confirmation).
KEY_DELIMITER = "|"

# Simulated billing period format from the business template.
PERIOD_PATTERN = r"^SIM-\d{4}-(0[1-9]|1[0-2])$"
SIM_CURRENCIES = ("CNY",)
SIM_WEIGHT_UOM = "kg"
BILL_AMOUNT_MAX_SCALE = 2  # ENG-SIM: template says simulated amounts are kept to cents

# ENG-SIM import limits (IMP-006). Real limits pending environment agreement.
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 100_000

ALLOWED_DELIMITERS = {"comma": ",", "tab": "\t", "semicolon": ";"}
ALLOWED_ENCODINGS = {"UTF-8", "UTF8"}

# Field whitelist per source table (English names from the business template).
# ENG-SIM addition: "remark" (free text) is registered as a sensitive pass-through
# field so that TC-12/TC-18/TC-19/TC-24 fixtures can carry placeholder text; it never
# participates in matching or calculation and never appears in any output.
RESERVED_NOT_USED = (
    "charge_name", "product_type", "contract_id", "lane_or_route", "rebill_seq",
    "actual_weight", "volume_weight", "tax_included_flag", "tax_code", "tax_rate",
    "tax_amount", "amount_ex_tax", "amount_inc_tax",
)
FIELD_WHITELIST = {
    TABLE_BILL: (
        "source_row_id", "waybill_no", "charge_code", "billing_period", "bill_amount",
        "currency", "chargeable_weight", "weight_uom", "remark",
    ) + RESERVED_NOT_USED,
    TABLE_WAYBILL: (
        "source_row_id", "waybill_no", "charge_code", "billing_period", "currency",
        "unit_price", "chargeable_weight", "surcharge_rate", "weight_uom", "remark",
    ) + RESERVED_NOT_USED,
}
SENSITIVE_FIELDS = ("remark", "contract_id")

REQUIRED_BILL_COLUMNS = ("waybill_no", "charge_code", "billing_period", "bill_amount", "currency")
REQUIRED_WAYBILL_KEY_COLUMNS = ("waybill_no", "charge_code")

# Registered rule input symbols -> waybill field.
INPUT_FIELDS = {"P": "unit_price", "W": "chargeable_weight", "R": "surcharge_rate"}

# ENG-SIM header alias dictionary used only to *propose* mappings. Proposals are never
# applied without a human confirmation (FR-MAP).
ALIAS_DICT_VERSION = "ENG-SIM-ALIAS-2026-11-12-A"
HEADER_ALIASES = {
    "行号": ["source_row_id"],
    "源表行号": ["source_row_id"],
    "运单号": ["waybill_no"],
    "运单编号": ["waybill_no"],
    "费用代码": ["charge_code"],
    "收费项代码": ["charge_code"],
    "收费项名称": ["charge_name"],
    "账单金额": ["bill_amount"],
    "账单金额（元）": ["bill_amount"],
    "账期": ["billing_period"],
    "币种": ["currency"],
    "单价": ["unit_price"],
    "计费重量": ["chargeable_weight"],
    "实际重量": ["actual_weight"],
    "体积重量": ["volume_weight"],
    "重量": ["chargeable_weight", "actual_weight"],
    "附加费率": ["surcharge_rate"],
    "重量单位": ["weight_uom"],
    "备注": ["remark"],
}

# Rule catalog conventions.
ALLOWED_ROUNDING_MODES = ("ROUND_HALF_UP",)
SIM_APPROVAL_STATUS = "模拟约定"
EVIDENCE_REQUIRED_FIELDS = (
    "source_table", "source_row_id", "candidate_row_ids", "import_batch_id", "match_key",
    "charge_code", "billing_period", "rule_version", "formula_id", "inputs", "intermediates",
    "rounding_mode", "rounding_scale", "rounding_layer", "output_amount", "diff_amount",
    "currency", "biz_status", "block_scope", "block_reason", "completeness_flag",
    "human_review_record",
)

# Mapping confirmation: identities that may never confirm (MAP-002).
NON_HUMAN_CONFIRMER_PREFIXES = ("AI", "SYSTEM", "SYS", "AUTO", "BOT", "LLM", "MAP-CAND")
CONFIRMED_AT_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$"

# CLI exit codes.
EXIT_OK = 0
EXIT_BATCH_BLOCKED = 2
EXIT_REFUSED = 3
EXIT_SECURITY = 4
EXIT_USAGE = 64
EXIT_INTERNAL = 70

# Result / error codes. Codes from R2 keep their R2 meaning; ENG-SIM codes are additions.
CODES = {
    "IMP-001": "编码非 UTF-8 或无法按 UTF-8 解码，拒绝导入",
    "IMP-002": "CSV 无法解析（含引号未闭合、列数不一致、表头重复）",
    "IMP-003": "空文件或无数据行",
    "IMP-004": "表头含未登记字段",
    "IMP-005": "检出 CSV 公式注入形态（=、+、@、非数值短横、制表/回车前缀），整批拒绝",
    "IMP-006": "超出设计上限（ENG-SIM 上限）",
    "IMP-007": "幂等键冲突：同键不同源哈希或目录哈希，拒绝覆盖",
    "IMP-008": "未声明已脱敏，拒绝接收",
    "IMP-010": "source_table 不在允许清单",
    "VAL-001": "必填列整列不存在",
    "VAL-002": "规则目录无法加载或版本未冻结",
    "VAL-003": "批次账期无法识别",
    "MAP-001": "字段映射未人工确认或 mapping_hash 不一致，拒绝进入自动核对",
    "MAP-002": "试图以 AI 或系统身份确认映射，拒绝",
    "MAP-003": "ENG-SIM：映射确认内容不合法（目标不在白名单、未覆盖全部源列、目标重复或时间无 +08:00）",
    "REC-002": "请求的 rule_version 或目录哈希与冻结快照不一致",
    "EXP-001": "批次或核对跑次不存在 / 导入未接受",
    "SEC-001": "可观察输出含敏感原文，拒绝写出",
    "SRC-001": "ENG-SIM：源文件副本哈希与导入时不一致",
    "OOS-001": "ENG-SIM：超出对账范围（记账/付款/改写源数据），拒绝",
    "SYS-001": "内部错误；不回写源数据、不返回敏感原文",
    "VAL-010": "单行必填字段缺失或不可解析",
    "VAL-011": "金额不可解析",
    "VAL-012": "币种缺失或超出约定",
    "VAL-013": "source_row_id 冲突",
    "VAL-014": "匹配键无法构造",
    "REC-010": "运单侧多候选（匹配不唯一）",
    "REC-011": "账单侧同键重复",
    "REC-012": "账单有运单无",
    "REC-013": "运单有账单无",
    "CAL-001": "规则未命中",
    "CAL-002": "必填输入缺失",
    "CAL-003": "附加费基数不可用",
    "CAL-004": "单位或币种冲突，禁止暗含换算",
    "CAL-005": "金额超出容差",
    "CAL-006": "ENG-SIM：依附的基础运费行未达核对通过候选，附加费不得自动通过",
    "RUL-001": "公式未登记",
    "RUL-002": "拒绝自由表达式求值",
}

# Row-level status priority (PRD ②–⑨ / R2 table 5–11). Lower index wins.
ROW_PRIORITY = [
    ({"VAL-014"}, ST_PENDING),
    ({"REC-010"}, ST_PENDING),
    ({"REC-011"}, ST_BILL_FAIL),
    ({"REC-012"}, ST_BILL_FAIL),
    ({"REC-013"}, ST_MISSED),
    ({"CAL-001", "CAL-002", "CAL-003", "CAL-004", "CAL-006", "RUL-001", "RUL-002",
      "VAL-010", "VAL-011", "VAL-012", "VAL-013"}, ST_PENDING),
    ({"CAL-005"}, ST_BILL_FAIL),
]
