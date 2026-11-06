"""紧急情况确定性拦截 —— 模型调用之前扫描用户输入。

S9.1 / F11：安全扫描必须先于一切 LLM 处理，判定为 CONFIRMED 时整轮只输出
固定的 120 模板，不调用模型、不执行任何工具、不追问任何信息。
实现为最外层 ``wrap_model_call``：命中时直接返回
``ModelResponse(result=[AIMessage(content=…固定文案)])``，**不调用 handler**
（跳过 handler 即短路，是 langchain 中间件文档化的拦截模式）。

SUSPICIOUS（否定 / 转述 / 假设语境）同样走确定性澄清文案，不经 LLM。

审计为尽力而为：异步路径下追加最小审计行（仅去标识键 + 事件类型），
数据库故障绝不影响 120 回复。
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from haven.messages.builder import builder
from haven.middleware._text import human_text
from haven.safety.emergency import (
    EMERGENCY_CLARIFY_TEXT,
    EMERGENCY_RESPONSE_CN,
    EmergencyLevel,
    detect_emergency,
)
from haven.storage.audit import record_audit, subject_key_for
from haven.storage.database import caller_user_id, session_scope


class EmergencyInputMiddleware(AgentMiddleware):
    """用户输入紧急词扫描 —— 纯确定性，绝不经 LLM。"""

    @property
    def name(self) -> str:
        return "haven_emergency_input"

    @staticmethod
    def _fixed_reply(request: ModelRequest) -> str | None:
        """命中返回固定文案；未命中返回 None（放行）。"""
        text = human_text(request)
        if not text:
            return None
        result = detect_emergency(text)
        if result.level is EmergencyLevel.CONFIRMED:
            return EMERGENCY_RESPONSE_CN
        if result.level is EmergencyLevel.SUSPICIOUS:
            return EMERGENCY_CLARIFY_TEXT
        return None

    async def _record_audit(self, request: ModelRequest, reason: str) -> None:
        """尽力而为的审计：失败静默，绝不影响回复。"""
        try:
            uid = caller_user_id(getattr(request, "runtime", None))
            async with session_scope() as session:
                await record_audit(
                    session,
                    subject_key=subject_key_for(uid) if uid else None,
                    event_type="emergency",
                    event_summary=f"emergency:{reason}",
                )
        except Exception:  # noqa: BLE001 —— 审计永远不许打断急救回复
            return

    def wrap_model_call(self, request: ModelRequest, handler):
        fixed = self._fixed_reply(request)
        if fixed is not None:
            # 不调用 handler：模型不运行、无工具、无追问。
            return ModelResponse(result=[builder.ai(fixed)])
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler):
        fixed = self._fixed_reply(request)
        if fixed is not None:
            result = detect_emergency(human_text(request))
            await self._record_audit(request, result.reason or "matched")
            return ModelResponse(result=[builder.ai(fixed)])
        return await handler(request)


emergency_input_middleware = EmergencyInputMiddleware()
