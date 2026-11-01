"""健健独立入口：单次对话，使用 LangChain，不经过 MDA 编译。

运行：uv run python haven.py "您好"
本阶段无对话检查点、人工确认、外部 MCP 或 HTTP 服务。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langsmith import tracing_context

from config.settings import get_settings
from messages.builder import builder
from middleware.commands import command_middleware
from middleware.consent_policy import consent_policy_middleware
from middleware.emergency_input import emergency_input_middleware
from middleware.mcp_policy import mcp_policy_middleware
from middleware.memory_context import MemoryInjectionMiddleware
from middleware.observability import observability_middleware
from middleware.output_safety import output_safety_middleware
from safety.degradation import LLM_DEGRADED_RESPONSE
from tools.account import delete_my_data
from tools.consent import consent_status, get_consent_policy, record_consent
from tools.onboarding import get_health_profile, save_health_profile, save_onboarding_draft
from tools.permissions import TOOL_PERMISSIONS
from tools.trends import get_seven_day_trend
from tools.vitals import (
    confirm_abnormal_blood_pressure,
    get_pending_blood_pressure,
    record_blood_pressure,
)

_PROJECT_ROOT = Path(__file__).resolve().parent
APPROVAL_UNAVAILABLE_REPLY = (
    "独立入口尚未接入人工确认，本次操作未执行。"
    "异常血压确认或数据删除请使用原有 MDA 入口完成，不要尝试绕过确认。"
)


class ApprovalUnavailableMiddleware(AgentMiddleware):
    """独立路径的临时工具闸门：所有要求人工确认的工具均拒绝执行。"""

    @property
    def name(self) -> str:
        return "haven_approval_unavailable"

    @staticmethod
    def _refusal(request: ToolCallRequest) -> ToolMessage | None:
        call = request.tool_call
        permission = TOOL_PERMISSIONS.get(call["name"])
        if permission is None or not permission.requires_approval:
            return None
        return builder.tool(
            APPROVAL_UNAVAILABLE_REPLY,
            tool_call_id=call.get("id") or "",
            name=call["name"],
        )

    def wrap_tool_call(self, request: ToolCallRequest, handler):
        refusal = self._refusal(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        refusal = self._refusal(request)
        return refusal if refusal is not None else await handler(request)


def create_haven(*, model: BaseChatModel | None = None):
    """创建独立 Agent，调用方使用 ainvoke；可注入离线模型用于验证。

    不在导入时创建模型。模型密钥由调用方提供的进程环境读取；CLI 会加载
    项目 .env。业务工具与中间件直接复用，保留原有相对顺序。
    """
    return create_agent(
        model=model if model is not None else get_settings().chat_model,
        system_prompt=(_PROJECT_ROOT / "instructions.md").read_text(encoding="utf-8"),
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
        middleware=[
            emergency_input_middleware,
            command_middleware,
            output_safety_middleware,
            MemoryInjectionMiddleware(inject_base_prompt=False),
            ApprovalUnavailableMiddleware(),  # 先拒绝，避免被记录为工具写入成功
            observability_middleware,
            consent_policy_middleware,
            mcp_policy_middleware,
        ],
        name="haven",
    )


async def run_once(text: str, *, model: BaseChatModel | None = None) -> str:
    """独立执行一轮对话；本阶段显式关闭追踪，不改变进程的追踪配置。

    工具或图执行发生未捕获异常时返回固定降级文案，不打印含健康数据的堆栈，
    不自动重试可能已发生的写入。模型故障继续由原有输出安全中间件处理。
    """
    if not text.strip():
        return "请输入您想咨询或记录的内容。"
    with tracing_context(enabled=False):
        try:
            agent = create_haven(model=model)
            result = await agent.ainvoke(
                {"messages": [HumanMessage(content=text)]},
                config={"recursion_limit": 50},
            )
            last = result["messages"][-1]
            if isinstance(last, AIMessage) and last.text:
                return last.text
        except Exception:  # noqa: BLE001 —— 不泄露异常参数，不重试业务写入
            pass
    return LLM_DEGRADED_RESPONSE


def main() -> int:
    """从参数或单次终端输入读取消息；沿用现有 .env 与模型配置。"""
    parser = argparse.ArgumentParser(description="健健独立入口（单次对话）")
    parser.add_argument("message", nargs="?", help="省略时在终端输入一条消息")
    args = parser.parse_args()
    try:
        # python-dotenv 已由 pydantic-settings 引入，不新增依赖或改动版本组合。
        load_dotenv(_PROJECT_ROOT / ".env", override=False)
        text = args.message if args.message is not None else input("您：")
        print(asyncio.run(run_once(text)))
        return 0
    except EOFError:
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 —— 配置或启动失败时不打印密钥和健康数据
        print("启动失败，请检查依赖、模型配置和 .env。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
