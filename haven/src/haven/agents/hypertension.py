"""高血压业务 Agent：将现有业务能力装配到通用 ReAct 子图。"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel

from haven.middleware.consent_policy import consent_policy_middleware
from haven.middleware.mcp_policy import mcp_policy_middleware
from haven.middleware.memory_context import MemoryInjectionMiddleware
from haven.middleware.observability import observability_middleware
from haven.middleware.output_safety import output_safety_middleware
from haven.reasoning import build_react_graph
from haven.resources import read_instructions
from haven.safety.degradation import LLM_DEGRADED_RESPONSE
from haven.tools.account import delete_my_data
from haven.tools.consent import consent_status, get_consent_policy, record_consent
from haven.tools.onboarding import get_health_profile, save_health_profile, save_onboarding_draft
from haven.tools.permissions import interrupt_on
from haven.tools.trends import get_seven_day_trend
from haven.tools.vitals import (
    confirm_abnormal_blood_pressure,
    get_pending_blood_pressure,
    record_blood_pressure,
)

_INSTRUCTIONS_PATH = "instructions.md"


def build_hypertension_agent(*, model: BaseChatModel):
    """装配不含独立检查点的业务子图，模型由项目入口创建并传入。

    急救输入、命令路由、线程和批准恢复由父图/会话负责；本子图不能直接作为
    面向用户的独立入口。由 haven.session 顶层图挂载，不依赖 MDA。
    """
    instructions = read_instructions(_INSTRUCTIONS_PATH)

    return build_react_graph(
        model=model,
        tools=[
            get_consent_policy,
            consent_status,
            record_consent,
            get_health_profile,
            save_health_profile,
            save_onboarding_draft,
            record_blood_pressure,
            get_pending_blood_pressure,
            confirm_abnormal_blood_pressure,
            get_seven_day_trend,
            delete_my_data,
        ],
        system_prompt=instructions,
        fallback_reply=LLM_DEGRADED_RESPONSE,
        model_middleware=[
            output_safety_middleware,
            MemoryInjectionMiddleware(inject_base_prompt=False),
        ],
        tool_middleware=[
            observability_middleware,
            consent_policy_middleware,
            mcp_policy_middleware,
        ],
        approval_rules=interrupt_on(),
    )
