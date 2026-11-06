"""工具错误码 —— 结构化分类，替代纯字符串判断。

每个错误路径对应一个枚举值。按准则 4：清晰、可解析、可验证。
下游可通过 error_code 做分类统计或告警，不再依赖 message 字符串匹配。
"""

from enum import StrEnum


class ToolErrorCode(StrEnum):
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    CONSENT_REQUIRED = "CONSENT_REQUIRED"
    DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
    INVALID_PARAM = "INVALID_PARAM"
    DUPLICATE_RECORD = "DUPLICATE_RECORD"
    PENDING_EXPIRED = "PENDING_EXPIRED"
    PENDING_MISMATCH = "PENDING_MISMATCH"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    PARSE_ERROR = "PARSE_ERROR"
    UNKNOWN = "UNKNOWN"


#: 错误码 → 人类可读标签（供日志/监控面板使用）。
ERROR_LABELS: dict[ToolErrorCode, str] = {
    ToolErrorCode.IDENTITY_MISMATCH: "身份不匹配",
    ToolErrorCode.CONSENT_REQUIRED: "未同意隐私政策",
    ToolErrorCode.DATABASE_UNAVAILABLE: "数据库不可用",
    ToolErrorCode.INVALID_PARAM: "参数校验失败",
    ToolErrorCode.DUPLICATE_RECORD: "重复记录",
    ToolErrorCode.PENDING_EXPIRED: "待确认记录已过期",
    ToolErrorCode.PENDING_MISMATCH: "待确认记录数值不一致",
    ToolErrorCode.FUTURE_TIMESTAMP: "测量时间在未来",
    ToolErrorCode.PARSE_ERROR: "时间解析失败",
    ToolErrorCode.UNKNOWN: "未知错误",
}
