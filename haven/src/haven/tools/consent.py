"""隐私同意工具 —— 0.0.1 一切健康数据操作之前的闸门。

同意为一次性（同调用者重复同意不落重复行、不重复审计）。
界面入口统一为 /consent 命令（中文「同意隐私政策」等说法仍可用）。
"""

from __future__ import annotations

from langchain.tools import ToolRuntime, tool
from sqlalchemy import select

from haven.application.messages import MSG
from haven.safety.disclaimers import DisclaimerType, get_disclaimer
from haven.schemas.tools import ConsentPolicyResult, ConsentRecordResult, ConsentStatusResult
from haven.storage.audit import record_audit, subject_key_for
from haven.storage.database import DatabaseUnavailable, session_scope
from haven.storage.models import ConsentRecord
from haven.tools._helpers import NO_IDENTITY_REPLY, OptionalToolRuntime, degraded, uid_of

POLICY_VERSION = "0.0.1"
#: 与 src 的 consent scope 保持一致。
SCOPE = "health_data_collection"


@tool
def get_consent_policy() -> ConsentPolicyResult:
    """展示隐私政策全文。

    首次使用必须先同意隐私政策，之后才能建档与记录健康数据。
    无需前置条件，任何人均可调用。
    """
    return ConsentPolicyResult(
        policy_version=POLICY_VERSION,
        message=get_disclaimer(DisclaimerType.FIRST_USE) + MSG.consent_guidance,
    )


@tool(parse_docstring=True)
async def consent_status(runtime: OptionalToolRuntime = None) -> ConsentStatusResult:
    """查询当前用户的隐私同意状态。

    未同意时返回隐私政策全文与同意指引；已同意时返回确认信息。
    """
    uid = uid_of(runtime)
    if uid is None:
        return ConsentStatusResult(ok=False, message=NO_IDENTITY_REPLY)
    try:
        async with session_scope() as session:
            stmt = select(ConsentRecord).where(ConsentRecord.user_id == uid).limit(1)
            result = await session.execute(stmt)
            consented = result.scalars().first() is not None
    except DatabaseUnavailable:
        return ConsentStatusResult(ok=False, message=degraded())
    if consented:
        return ConsentStatusResult(
            ok=True, consented=True, policy_version=POLICY_VERSION, message=MSG.consent_already
        )
    return ConsentStatusResult(
        ok=True,
        consented=False,
        message=get_disclaimer(DisclaimerType.FIRST_USE) + MSG.consent_guidance,
    )


@tool(parse_docstring=True)
async def record_consent(runtime: OptionalToolRuntime = None) -> ConsentRecordResult:
    """记录当前用户的隐私同意（政策版本 0.0.1）。

    仅在用户明确表示同意后调用。重复调用幂等：已有同意记录时返回确认而不重复写入。
    """
    return await record_consent_action(runtime)


async def record_consent_action(runtime: ToolRuntime | None) -> ConsentRecordResult:
    """供工具和 `/consent` 命令共用的确定性同意逻辑。"""
    uid = uid_of(runtime)
    if uid is None:
        return ConsentRecordResult(ok=False, message=NO_IDENTITY_REPLY)
    try:
        async with session_scope() as session:
            stmt = select(ConsentRecord).where(ConsentRecord.user_id == uid).limit(1)
            result = await session.execute(stmt)
            if result.scalars().first() is not None:
                return ConsentRecordResult(
                    ok=True,
                    consented=True,
                    policy_version=POLICY_VERSION,
                    message=MSG.consent_already,
                )
            session.add(
                ConsentRecord(
                    user_id=uid,
                    policy_version=POLICY_VERSION,
                    scope=SCOPE,
                )
            )
            await record_audit(
                session,
                subject_key=subject_key_for(uid),
                event_type="consent",
                event_summary="consent:granted",
            )
    except DatabaseUnavailable:
        return ConsentRecordResult(ok=False, message=degraded())
    return ConsentRecordResult(
        ok=True,
        consented=True,
        policy_version=POLICY_VERSION,
        message=MSG.consent_granted,
    )
