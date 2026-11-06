"""MCP 工具调用策略 —— 只读白名单、审计与降级（工具层钩子）。

外部 MCP 工具与自有工具共用同一个工具节点，但它们**不走**本项目的同意闸门
与确定性校验，所以这里补一层策略：

- **默认拒绝**：只有「已配置服务器 + 白名单内」的 `{server}_{tool}` 放行，
  其余 MCP 前缀工具一律拒绝（直接返回固定文案，**不调用 handler**）；
- **失败降级**：远端异常/超时 → 固定降级文案（要求模型如实说"没查到"）；
- **审计**：异步路径每次调用写一条去标识审计（工具名 + 结果，不含参数），
  best-effort —— 审计失败绝不影响工具返回；
- **非 MCP 工具完全透传**（零行为变化）。同步路径同样做策略与降级，但不写
  审计：同步上下文没有事件循环，且不允许在同步路径里跑异步 IO。

只读是"双保险"之一：另一道在连接器侧（`include_tools`）；最终写权限仍取决于
MCP 服务器自身（如 Neo4j 侧只读开关）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from haven.config.settings import McpServerSettings, get_settings
from haven.messages.builder import builder
from haven.safety.degradation import MCP_DEGRADED_RESPONSE, MCP_DENIED_RESPONSE
from haven.storage.audit import record_audit, subject_key_for
from haven.storage.database import session_scope
from haven.tools._helpers import uid_of


class McpPolicyMiddleware(AgentMiddleware):
    """MCP 命名空间的默认拒绝 + 审计 + 降级。"""

    @property
    def name(self) -> str:
        return "haven_mcp_policy"

    @staticmethod
    def _server_of(
        tool_name: str, servers: Mapping[str, McpServerSettings]
    ) -> str | None:
        for server in servers:
            if tool_name.startswith(f"{server}_"):
                return server
        return None

    @staticmethod
    def _allowlist(servers: Mapping[str, McpServerSettings]) -> frozenset[str]:
        return frozenset(
            f"{server}_{tool}"
            for server, config in servers.items()
            for tool in config.include_tools
        )

    @staticmethod
    def _refusal(request: ToolCallRequest, text: str):
        call = request.tool_call
        return builder.tool(
            text,
            tool_call_id=call.get("id") or "",
            name=call.get("name", ""),
        )

    @staticmethod
    async def _audit(request: ToolCallRequest, tool_name: str, outcome: str) -> None:
        """去标识审计（工具名 + 结果，绝不记参数）。best-effort。"""
        uid = uid_of(getattr(request, "runtime", None))
        if uid is None:
            return
        try:
            async with session_scope() as session:
                await record_audit(
                    session,
                    subject_key=subject_key_for(uid),
                    event_type="mcp",
                    event_summary=f"mcp:{tool_name}:{outcome}",
                )
        except Exception:  # noqa: BLE001 —— 审计失败不影响工具结果
            return

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        name = str(request.tool_call.get("name", ""))
        servers = get_settings().mcp_servers
        if self._server_of(name, servers) is None:
            return handler(request)
        if name not in self._allowlist(servers):
            return self._refusal(request, MCP_DENIED_RESPONSE)
        try:
            return handler(request)
        except Exception:  # noqa: BLE001 —— 远端失败一律降级，不抛给模型
            return self._refusal(request, MCP_DEGRADED_RESPONSE)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        name = str(request.tool_call.get("name", ""))
        servers = get_settings().mcp_servers
        if self._server_of(name, servers) is None:
            return await handler(request)
        if name not in self._allowlist(servers):
            await self._audit(request, name, "denied")
            return self._refusal(request, MCP_DENIED_RESPONSE)
        try:
            result = await handler(request)
        except Exception:  # noqa: BLE001 —— 远端失败一律降级，不抛给模型
            await self._audit(request, name, "error")
            return self._refusal(request, MCP_DEGRADED_RESPONSE)
        await self._audit(request, name, "ok")
        return result


mcp_policy_middleware = McpPolicyMiddleware()
