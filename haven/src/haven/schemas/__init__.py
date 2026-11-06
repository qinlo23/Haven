"""结构化输出定义包 —— 集中管理所有 Pydantic 模型。

本包按用途分子模块，每个模型注明其消费方（哪个中间件/工具/节点使用它）。

使用方式：
    from haven.schemas.xxx import SomeModel
    model.with_structured_output(SomeModel)
"""

from haven.schemas.intent import UserIntent
from haven.schemas.response import AgentResponse
from haven.schemas.safety import SafetyVerdict
from haven.schemas.tools import (
    BpConfirmResult,
    BpPendingResult,
    BpRecordResult,
    ConsentPolicyResult,
    ConsentRecordResult,
    ConsentStatusResult,
    DeleteResult,
    OnboardingDraftResult,
    OnboardingProfileResult,
    OnboardingSaveResult,
    TrendResult,
)

__all__ = [
    "SafetyVerdict",
    "AgentResponse",
    "UserIntent",
    "BpRecordResult",
    "BpPendingResult",
    "BpConfirmResult",
    "TrendResult",
    "OnboardingProfileResult",
    "OnboardingSaveResult",
    "OnboardingDraftResult",
    "ConsentPolicyResult",
    "ConsentStatusResult",
    "ConsentRecordResult",
    "DeleteResult",
]
