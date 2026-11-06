"""为显式状态图组合原生异步中间件钩子。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

ModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]
ToolHandler = Callable[[ToolCallRequest], Awaitable[ToolMessage]]


def compose_model_handler(
    middleware: Sequence[AgentMiddleware], handler: ModelHandler
) -> ModelHandler:
    """按声明顺序从外到内包裹模型调用；不隐式执行生命周期钩子。"""

    def wrap(item: AgentMiddleware, inner: ModelHandler) -> ModelHandler:
        async def call(request: ModelRequest) -> ModelResponse:
            return await item.awrap_model_call(request, inner)

        return call

    for item in reversed(middleware):
        if type(item).awrap_model_call is AgentMiddleware.awrap_model_call:
            raise TypeError("模型中间件必须实现 awrap_model_call。")
        handler = wrap(item, handler)
    return handler


def compose_tool_handler(
    middleware: Sequence[AgentMiddleware], handler: ToolHandler
) -> ToolHandler:
    """按声明顺序从外到内包裹单次工具调用。"""

    def wrap(item: AgentMiddleware, inner: ToolHandler) -> ToolHandler:
        async def call(request: ToolCallRequest) -> ToolMessage:
            return await item.awrap_tool_call(request, inner)

        return call

    for item in reversed(middleware):
        if type(item).awrap_tool_call is AgentMiddleware.awrap_tool_call:
            raise TypeError("工具中间件必须实现 awrap_tool_call。")
        handler = wrap(item, handler)
    return handler
