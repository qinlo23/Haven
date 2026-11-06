"""同意闸门中间件 —— 在工具调用前统一拦截，替代工具内部的内联判断。

对照 `tools/permissions.py`：`consent_required=True` 的工具在进入前
校验用户是否已同意隐私政策。未同意 → 直接返回固定拒绝文案，不进入工具函数。

非本地工具（不在权限表中的）完全透传，零行为变化。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from haven.application.messages import MSG
from haven.messages.builder import builder
from haven.storage.database import DatabaseUnavailable, session_scope
from haven.tools._helpers import NO_IDENTITY_REPLY, has_consented, uid_of
from haven.tools.permissions import TOOL_PERMISSIONS


class ConsentPolicyMiddleware(AgentMiddleware):
    """同意闸门：查权限表 → 校验同意 → 拒绝或放行。"""

    @property
    def name(self) -> str:
        return "haven_consent_policy"

    @staticmethod
    def _refusal(request: ToolCallRequest, text: str) -> ToolMessage:
        call = request.tool_call
        return builder.tool(
            text,
            tool_call_id=call.get("id") or "",
            name=call.get("name", ""),
        )

    def _needs_check(self, tool_name: str) -> bool:
        perm = TOOL_PERMISSIONS.get(tool_name)
        return perm is not None and perm.consent_required

    # ── 同步路径：简单拦截（实际 I/O 工具都是异步，同步路径保守透传） ──
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        name = str(request.tool_call.get("name", ""))
        if not self._needs_check(name):
            return handler(request)
        uid = uid_of(getattr(request, "runtime", None))
        if uid is None:
            return self._refusal(request, NO_IDENTITY_REPLY)
        return handler(request)

    # ── 异步路径：执行同意闸门 ──
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        name = str(request.tool_call.get("name", ""))
        if not self._needs_check(name):
            return await handler(request)

        uid = uid_of(getattr(request, "runtime", None))
        if uid is None:
            return self._refusal(request, NO_IDENTITY_REPLY)

        try:
            async with session_scope() as session:
                if not await has_consented(session, uid):
                    return self._refusal(request, MSG.consent_required)
        except DatabaseUnavailable:
            return self._refusal(request, MSG.error_fallback)

        return await handler(request)


consent_policy_middleware = ConsentPolicyMiddleware()
