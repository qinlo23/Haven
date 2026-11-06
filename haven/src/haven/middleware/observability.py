"""工具调用可观测性中间件 —— 每一跳都可见。

按准则 5（幂等性与可观测性）实现：
- 统一工具调用日志：tool_call_id + 结果 + 耗时（不含健康数据参数）
- 审计覆盖：所有 `audit_write=True` 的写操作自动落审计日志
- 日志用 Python logging 输出 JSON 行，方便接入 ELK/Loki 等日志平台
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from haven.storage.audit import record_audit, subject_key_for
from haven.storage.database import DatabaseUnavailable, session_scope
from haven.tools._helpers import uid_of
from haven.tools.permissions import TOOL_PERMISSIONS

_logger = logging.getLogger("haven.observability")


class ObservabilityMiddleware(AgentMiddleware):
    """工具调用计时 + 审计（写操作）+ tool_call_id 追溯。"""

    @property
    def name(self) -> str:
        return "haven_observability"

    # ── 同步路径：计时但不写审计（工具均为异步，同步路径保守透传） ──
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        name = str(request.tool_call.get("name", ""))
        call_id = str(request.tool_call.get("id", ""))
        start = time.monotonic()
        try:
            result = handler(request)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _log_metric(name, call_id, "ok", elapsed_ms)
            return result
        except Exception:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _log_metric(name, call_id, "exception", elapsed_ms)
            raise

    # ── 异步路径：计时 + 审计 ──
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        name = str(request.tool_call.get("name", ""))
        call_id = str(request.tool_call.get("id", ""))
        start = time.monotonic()
        try:
            result = await handler(request)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _log_metric(name, call_id, "ok", elapsed_ms)
            await _maybe_audit(request, name, "ok")
            return result
        except Exception:  # noqa: BLE001
            elapsed_ms = int((time.monotonic() - start) * 1000)
            _log_metric(name, call_id, "exception", elapsed_ms)
            await _maybe_audit(request, name, "exception")
            raise


def _log_metric(tool_name: str, call_id: str, outcome: str, elapsed_ms: int) -> None:
    _logger.info(
        json.dumps(
            {
                "event": "tool_call",
                "tool": tool_name,
                "call_id": call_id,
                "outcome": outcome,
                "elapsed_ms": elapsed_ms,
            },
            ensure_ascii=False,
        )
    )


async def _maybe_audit(
    request: ToolCallRequest,
    tool_name: str,
    outcome: str,
) -> None:
    """仅在权限表声明 audit_write=True 时写审计。best-effort。"""
    perm = TOOL_PERMISSIONS.get(tool_name)
    if perm is None or not perm.audit_write:
        return

    uid = uid_of(getattr(request, "runtime", None))
    if uid is None:
        return

    try:
        async with session_scope() as session:
            await record_audit(
                session,
                subject_key=subject_key_for(uid),
                event_type="tool_write",
                event_summary=f"tool:{tool_name}:{outcome}",
            )
    except (DatabaseUnavailable, Exception):  # noqa: BLE001 —— 审计失败不影响工具结果
        return


observability_middleware = ObservabilityMiddleware()
