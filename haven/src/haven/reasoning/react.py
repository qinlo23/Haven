"""显式 ReAct 子图：思考、审批、单步行动及观察，不装配项目业务。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Annotated, Any, Literal, NotRequired, TypedDict, cast

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from haven.reasoning.middleware import compose_model_handler, compose_tool_handler

REJECTED_TOOL_REPLY = "本次工具操作未获批准，未执行。请勿自行重试，等待用户重新提出请求。"
TOOL_FAILED_REPLY = "本次工具操作未正常完成，结果未知。请如实告知用户核实数据，不要自动重试。"
INVALID_REVIEW_REPLY = "确认信息无效，操作未执行。请重新启动会话。"


class ReactState(TypedDict):
    """消息以 ID 合并；其余字段由节点覆盖，父图负责新请求初始化。"""

    messages: Annotated[list[AnyMessage], add_messages]
    handled: NotRequired[bool]
    model_calls: NotRequired[int]
    pending_tools: list[ToolCall]
    tool_index: int
    review_decisions: NotRequired[dict[str, Literal["approve", "reject"]]]


class InvalidReview(ValueError):
    """确认载荷非法；调用方应停止会话，不重试业务写入。"""


def build_react_graph(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    system_prompt: str,
    fallback_reply: str,
    model_middleware: Sequence[AgentMiddleware] = (),
    tool_middleware: Sequence[AgentMiddleware] = (),
    approval_rules: Mapping[str, bool] | None = None,
    max_model_calls: int = 8,
):
    """编译不含独立检查点的子图，由父图提供检查点和固定线程。

    父图在每条新用户请求时重置 model_calls、pending_tools、tool_index 和
    review_decisions。中断恢复不得执行该初始化。仅支持普通工具结果，不支持
    工具返回图路由 Command；所有业务提示与策略均由调用方传入。
    """
    if type(max_model_calls) is not int or max_model_calls < 1:
        raise ValueError("模型调用上限必须是正整数。")
    if not fallback_reply.strip():
        raise ValueError("必须提供非空降级文案。")
    tool_list = list(tools)
    names = {tool.name for tool in tool_list}
    if len(names) != len(tool_list):
        raise ValueError("工具名称不能重复。")
    rules = dict(approval_rules or {})
    if any(name not in names or type(required) is not bool for name, required in rules.items()):
        raise ValueError("审批规则必须对应已注册工具，值必须为布尔值。")
    # 在构建阶段检查钩子能力，避免执行到中途才发现传入了同步钩子。
    model_layers = tuple(model_middleware)
    tool_layers = tuple(tool_middleware)

    async def unused_model(request: ModelRequest) -> ModelResponse:
        return ModelResponse(result=[])

    async def unused_tool(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="", tool_call_id="")

    compose_model_handler(model_layers, unused_model)
    compose_tool_handler(tool_layers, unused_tool)

    def cleared() -> dict[str, Any]:
        return {"pending_tools": [], "tool_index": 0, "review_decisions": {}}

    def fallback(count: int) -> dict[str, Any]:
        return {**cleared(), "model_calls": count, "messages": [AIMessage(content=fallback_reply)]}

    async def reason(state: ReactState, config: RunnableConfig, runtime: Runtime):
        count = state.get("model_calls", 0)
        if count >= max_model_calls:
            return fallback(count)
        attempts = 0

        async def invoke_model(request: ModelRequest) -> ModelResponse:
            nonlocal attempts
            # 即使中间件多次调用 handler，每次模型尝试也必须消耗同一预算。
            if count + attempts >= max_model_calls:
                return ModelResponse(result=[AIMessage(content=fallback_reply)])
            attempts += 1
            settings = dict(request.model_settings)
            if request.tool_choice is not None:
                settings["tool_choice"] = request.tool_choice
            bound = (
                request.model.bind_tools(request.tools, **settings)
                if request.tools
                else request.model.bind(**settings)
            )
            messages = list(request.messages)
            if request.system_message is not None:
                messages.insert(0, request.system_message)
            result = await bound.ainvoke(messages, config=config)
            return ModelResponse(result=[result])

        request = ModelRequest(
            model=model,
            tools=tool_list,
            messages=list(state["messages"]),
            system_message=SystemMessage(content=system_prompt),
            state=cast("AgentState[Any]", state),
            runtime=runtime,
        )
        try:
            response = await compose_model_handler(model_layers, invoke_model)(request)
        except Exception:  # noqa: BLE001 —— 不记录原始异常或模型输入
            return fallback(count + attempts)
        count += attempts
        if len(response.result) != 1 or not isinstance(response.result[0], AIMessage):
            return fallback(count)
        message = response.result[0]
        if message.invalid_tool_calls:
            return fallback(count)
        calls = message.tool_calls
        ids = [call.get("id") for call in calls]
        if calls and (
            any(not isinstance(call_id, str) or not call_id.strip() for call_id in ids)
            or len(set(ids)) != len(ids)
        ):
            # 无效消息尚未写入历史，整批不执行，也不留下无法闭合的工具调用。
            return fallback(count)
        if not calls and not message.text:
            return fallback(count)
        return {
            **cleared(),
            "model_calls": count,
            "messages": [message],
            "pending_tools": deepcopy(calls),
        }

    def review(state: ReactState):
        sensitive = [call for call in state["pending_tools"] if rules.get(call["name"], False)]
        if not sensitive:
            return {"review_decisions": {}}
        response = interrupt(
            {
                "action_requests": [
                    {
                        "name": call["name"],
                        "args": deepcopy(call["args"]),
                        "description": "此操作需要明确批准。",
                    }
                    for call in sensitive
                ],
                "review_configs": [
                    {"action_name": call["name"], "allowed_decisions": ["approve", "reject"]}
                    for call in sensitive
                ],
            }
        )
        decisions = response.get("decisions") if isinstance(response, dict) else None
        if not isinstance(decisions, list) or len(decisions) != len(sensitive):
            raise InvalidReview(INVALID_REVIEW_REPLY)
        if any(
            not isinstance(item, dict)
            or set(item) != {"type"}
            or item["type"] not in ("approve", "reject")
            for item in decisions
        ):
            raise InvalidReview(INVALID_REVIEW_REPLY)
        return {
            "review_decisions": {
                call["id"]: decision["type"]
                for call, decision in zip(sensitive, decisions, strict=True)
            }
        }

    async def guarded_tool(request: ToolCallRequest, handler):
        state = request.state
        expected = deepcopy(state["pending_tools"][state["tool_index"]])
        decisions = dict(state.get("review_decisions", {}))

        def allowed(inner: ToolCallRequest) -> bool:
            return inner.tool_call == expected and (
                not rules.get(expected["name"], False) or decisions.get(expected["id"]) == "approve"
            )

        def refusal() -> ToolMessage:
            return ToolMessage(
                content=REJECTED_TOOL_REPLY,
                name=expected["name"],
                tool_call_id=expected["id"] or "",
                status="error",
            )

        async def execute(inner: ToolCallRequest):
            # 中间件之后仍核对冻结的调用，不能改名或改参数绕过审批。
            return await handler(inner) if allowed(inner) else refusal()

        if not allowed(request):
            return refusal()
        result = await compose_tool_handler(tool_layers, execute)(request)
        if not isinstance(result, ToolMessage):
            raise TypeError("工具必须返回普通工具消息，不能路由状态图。")
        return result

    tool_node = ToolNode(
        tool_list,
        awrap_tool_call=guarded_tool,
        handle_tool_errors=TOOL_FAILED_REPLY,
    )

    async def act(state: ReactState, config: RunnableConfig):
        index = state["tool_index"]
        call = state["pending_tools"][index]
        messages = list(state["messages"])
        for position in range(len(messages) - 1, -1, -1):
            if isinstance(messages[position], AIMessage):
                messages[position] = messages[position].model_copy(
                    update={"tool_calls": [deepcopy(call)]}
                )
                break
        else:
            raise ValueError("工具执行缺少模型调用消息。")
        view = {**state, "messages": messages}
        try:
            output = await tool_node.ainvoke(view, config=config)
            results = output["messages"]
            if (
                len(results) != 1
                or not isinstance(results[0], ToolMessage)
                or results[0].tool_call_id != call["id"]
            ):
                raise ValueError("工具结果未与调用匹配。")
            result = results[0]
        except Exception:  # noqa: BLE001 —— 参数或执行异常只回传固定文案
            result = ToolMessage(
                content=TOOL_FAILED_REPLY,
                name=call["name"],
                tool_call_id=call["id"] or "",
                status="error",
            )
        update = {"messages": [result], "tool_index": index + 1}
        if index + 1 == len(state["pending_tools"]):
            update.update(cleared())
        return update

    graph = StateGraph(ReactState)
    graph.add_node("reason", reason)
    graph.add_node("review", review)
    graph.add_node("act", act)
    graph.add_edge(START, "reason")
    graph.add_conditional_edges(
        "reason", lambda state: "review" if state.get("pending_tools") else END
    )
    graph.add_edge("review", "act")
    graph.add_conditional_edges(
        "act", lambda state: "act" if state.get("pending_tools") else "reason"
    )
    return graph.compile()
