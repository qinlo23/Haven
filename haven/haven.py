"""健健独立入口：进程内多轮对话与人工确认，不经过 MDA 编译。

运行：uv run python haven.py；带消息参数时执行单次对话。
检查点仅在进程内保存，本阶段无外部 MCP 或 HTTP 服务。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from dotenv import load_dotenv
from langchain.agents.middleware import AgentState, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, Interrupt
from langsmith import tracing_context

from agents.hypertension import build_hypertension_agent
from config.settings import get_settings
from middleware.commands import command_middleware
from middleware.emergency_input import emergency_input_middleware
from reasoning import ReactState
from reasoning.middleware import compose_model_handler
from safety.degradation import LLM_DEGRADED_RESPONSE
from safety.emergency import (
    EMERGENCY_CLARIFY_TEXT,
    EMERGENCY_RESPONSE_CN,
    EmergencyLevel,
    detect_emergency,
)

_PROJECT_ROOT = Path(__file__).resolve().parent


def create_haven(*, model: BaseChatModel | None = None):
    """创建独立 Agent，调用方使用 ainvoke；可注入离线模型用于验证。

    不在导入时创建模型。模型密钥由调用方提供的进程环境读取；CLI 会加载
    项目 .env。顶层处理急救与命令，业务子图处理 ReAct，统一持有内存检查点。
    """
    selected_model = model if model is not None else get_settings().chat_model
    business = build_hypertension_agent(model=selected_model)

    async def entry(state: ReactState, runtime: Runtime):
        # 仅新用户请求会经过此节点；子图中断恢复不会重新初始化请求预算。
        reset = {
            "handled": False,
            "model_calls": 0,
            "pending_tools": [],
            "tool_index": 0,
            "review_decisions": {},
        }
        forwarded = False

        async def unhandled(request: ModelRequest) -> ModelResponse:
            nonlocal forwarded
            forwarded = True
            return ModelResponse(result=[])

        handler = compose_model_handler([emergency_input_middleware, command_middleware], unhandled)
        response = await handler(
            ModelRequest(
                model=selected_model,
                messages=list(state["messages"]),
                state=cast("AgentState[Any]", {**state, **reset}),
                runtime=runtime,
            )
        )
        if forwarded:
            return reset
        if len(response.result) == 1 and isinstance(response.result[0], AIMessage):
            return {**reset, "handled": True, "messages": response.result}
        return {**reset, "handled": True, "messages": [AIMessage(content=LLM_DEGRADED_RESPONSE)]}

    graph = StateGraph(ReactState)
    graph.add_node("entry", entry)
    graph.add_node("hypertension", business)
    graph.add_edge(START, "entry")
    graph.add_conditional_edges(
        "entry", lambda state: END if state.get("handled") else "hypertension"
    )
    graph.add_edge("hypertension", END)
    return graph.compile(checkpointer=InMemorySaver(), name="haven")


@dataclass(frozen=True)
class SessionReply:
    """一轮对话的可展示结果；不向终端输出原始异常。"""

    text: str
    pending: bool = False
    failed: bool = False


class HavenSession:
    """本地单会话：固定线程、内存检查点、串行提交与明确批准/拒绝。

    每个实例持有独立的 Agent 和检查点。发生未捕获的运行异常后终止会话，
    不自动重试结果未知的写入；健康业务数据仍由现有工具管理。
    """

    def __init__(self, *, model: BaseChatModel | None = None):
        self.thread_id = f"standalone-{uuid4()}"
        self._agent = create_haven(model=model)
        self._pending: tuple[Interrupt, ...] = ()
        self._failed = False
        self._lock = asyncio.Lock()

    def _approval_prompt(self) -> str:
        lines = ["以下操作尚未执行，需要您确认："]
        for item in self._pending:
            for action in item.value["action_requests"]:
                args = json.dumps(action["args"], ensure_ascii=False)
                lines.append(f"- {action['name']}，参数：{args}")
        lines.append("输入 /approve 批准以上全部操作，或 /reject 拒绝；/exit 退出。")
        return "\n".join(lines)

    async def send(self, text: str) -> SessionReply:
        """提交消息或确认命令；待确认时不接收新的普通对话。"""
        async with self._lock:
            with tracing_context(enabled=False):
                try:
                    return await self._send(text.strip())
                except Exception:  # noqa: BLE001 —— 不打印参数，不重试未知写入
                    self._failed = True
                    return SessionReply(
                        LLM_DEGRADED_RESPONSE + "\n本次运行未正常完成，请核实数据后重新启动。",
                        failed=True,
                    )

    async def _send(self, text: str) -> SessionReply:
        if self._failed:
            return SessionReply("本会话已停止，请核实数据后重新启动。", failed=True)
        if not text:
            return SessionReply("请输入您想咨询或记录的内容。", pending=bool(self._pending))

        if self._pending:
            # 恢复会从中断节点继续，无法经过模型前急救钩子；先扫描本次输入。
            emergency = detect_emergency(text)
            if emergency.level is not EmergencyLevel.NONE:
                reply = (
                    EMERGENCY_RESPONSE_CN
                    if emergency.level is EmergencyLevel.CONFIRMED
                    else EMERGENCY_CLARIFY_TEXT
                )
                return SessionReply(reply, pending=True)
            if text not in {"/approve", "/reject"}:
                return SessionReply(self._approval_prompt(), pending=True)
            decision = "approve" if text == "/approve" else "reject"
            agent_input = Command(
                resume={
                    item.id: {
                        "decisions": [{"type": decision} for _ in item.value["action_requests"]]
                    }
                    for item in self._pending
                }
            )
        else:
            if text in {"/approve", "/reject"}:
                return SessionReply("当前没有待确认操作。")
            agent_input = {"messages": [HumanMessage(content=text)]}

        result = await self._agent.ainvoke(
            agent_input,
            config={"configurable": {"thread_id": self.thread_id}, "recursion_limit": 50},
        )
        self._pending = tuple(result.get("__interrupt__", ()))
        if self._pending:
            return SessionReply(self._approval_prompt(), pending=True)
        last = result["messages"][-1]
        if isinstance(last, AIMessage) and last.text:
            return SessionReply(last.text)
        self._failed = True
        return SessionReply(LLM_DEGRADED_RESPONSE, failed=True)


async def run_once(text: str, *, model: BaseChatModel | None = None) -> str:
    """兼容单次调用：敏感操作仍暂停，退出时不保存此次待确认运行。"""
    with tracing_context(enabled=False):
        try:
            reply = await HavenSession(model=model).send(text)
            if reply.pending:
                return reply.text + "\n单次模式不接收批准，请以交互模式重新发起操作。"
            return reply.text
        except Exception:  # noqa: BLE001 —— 启动失败返回固定降级文案
            return LLM_DEGRADED_RESPONSE


def chat() -> None:
    """本地多轮交互；退出即释放对话和确认中断的内存状态。"""
    # 主线程读取输入，避免 Ctrl+C 后执行器仍等待 input 导致退出挂起。
    # Runner 保持同一事件循环，模型的异步客户端和数据库连接可跨轮复用。
    with asyncio.Runner() as runner:
        session = HavenSession()
        print("健健独立模式：/exit 退出；退出后对话与确认中断清空，业务数据保留在数据库。")
        while True:
            text = input("您：")
            if text.strip() == "/exit":
                return
            reply = runner.run(session.send(text))
            print(f"健健：{reply.text}")
            if reply.failed:
                return


def main() -> int:
    """无参数进入多轮交互，带消息参数保留单次模式。"""
    parser = argparse.ArgumentParser(description="健健独立入口（多轮对话与人工确认）")
    parser.add_argument("message", nargs="?", help="单次消息；省略时进入多轮交互")
    args = parser.parse_args()
    try:
        # python-dotenv 已由 pydantic-settings 引入，不新增依赖或改动版本组合。
        load_dotenv(_PROJECT_ROOT / ".env", override=False)
        if args.message is None:
            chat()
        else:
            print(asyncio.run(run_once(args.message)))
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
