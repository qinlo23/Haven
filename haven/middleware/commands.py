"""模型前确定性路由；纯问好无需身份、数据库或模型。"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from sqlalchemy import select

from application.commands import (
    C_CANCEL,
    C_CONSENT,
    C_HELLO,
    C_HELP,
    is_greeting,
    match_command,
)
from application.messages import MSG
from messages.builder import builder
from middleware._text import human_text
from storage.database import DatabaseUnavailable, session_scope
from storage.models import OnboardingDraft, PendingBpConfirmation
from tools._helpers import NO_IDENTITY_REPLY, uid_of
from tools.consent import record_consent_action


class CommandMiddleware(AgentMiddleware):
    """命令消息的确定性路由（/hello /help /consent /cancel）。"""

    @property
    def name(self) -> str:
        return "haven_commands"

    @staticmethod
    def _static_reply(command: str) -> str | None:
        if command == C_HELLO:
            return MSG.greeting
        if command == C_HELP:
            return MSG.help_text()
        return None

    @staticmethod
    def _respond(text: str) -> ModelResponse:
        return ModelResponse(result=[builder.ai(text)])

    async def _cancel(self, request: ModelRequest) -> ModelResponse | None:
        """/cancel：清掉待确认血压行**和**建档草稿，按实际清掉的内容回复。

        两件事都要做，且必须在同一个 `session_scope` 里查/删 ——
        用户说"取消"时不会区分自己是在记血压还是在建档。**不能一查到
        None 就早返回**：只取消建档时待确认行本就不存在，早返回会让草稿
        永远删不掉，记忆块里会一直报着"未完的建档进度"。
        """
        uid = uid_of(getattr(request, "runtime", None))
        if uid is None:
            return self._respond(NO_IDENTITY_REPLY)
        pending = draft = None
        try:
            async with session_scope() as session:
                stmt = (
                    select(PendingBpConfirmation)
                    .where(PendingBpConfirmation.user_id == uid)
                    .limit(1)
                )
                pending = (await session.execute(stmt)).scalars().first()
                if pending is not None:
                    await session.delete(pending)
                stmt = select(OnboardingDraft).where(OnboardingDraft.user_id == uid).limit(1)
                draft = (await session.execute(stmt)).scalars().first()
                if draft is not None:
                    await session.delete(draft)
        except DatabaseUnavailable:
            return self._respond(MSG.error_fallback)

        if pending is not None:
            return self._respond(MSG.bp_cancelled)
        if draft is not None:
            return self._respond(MSG.onboarding_cancelled)
        return self._respond(MSG.cancel_none)

    def wrap_model_call(self, request: ModelRequest, handler):
        """同步路径：问好与帮助静态短路，其他消息继续处理。"""
        text = human_text(request)
        if is_greeting(text):
            return self._respond(MSG.greeting)
        command = match_command(text)
        static = self._static_reply(command) if command else None
        if static is not None:
            return self._respond(static)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler):
        text = human_text(request)
        if is_greeting(text):
            return self._respond(MSG.greeting)
        command = match_command(text)
        static = self._static_reply(command) if command else None
        if static is not None:
            return self._respond(static)
        if command == C_CONSENT:
            result = await record_consent_action(getattr(request, "runtime", None))
            return self._respond(result.message)
        if command == C_CANCEL:
            reply = await self._cancel(request)
            if reply is not None:
                return reply
        return await handler(request)


command_middleware = CommandMiddleware()
