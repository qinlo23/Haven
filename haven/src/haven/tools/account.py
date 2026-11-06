"""账户数据删除工具 —— 用户明示「删除」后整档清除（双保险：中断门 + 固定确认语）。

删除 6 张业务表（健康档案、健康问题、血压、同意、待确认、建档草稿），
审计日志仅追加一条去标识的 deletion 事件 —— 删除行为本身可追溯（合规要求），
健康数据不可恢复。
"""

from __future__ import annotations

from langchain.tools import tool
from sqlalchemy import delete

from haven.application.messages import MSG
from haven.schemas.tools import DeleteResult
from haven.storage.audit import record_audit, subject_key_for
from haven.storage.database import DatabaseUnavailable, session_scope
from haven.storage.models import (
    BloodPressure,
    ConsentRecord,
    DiseaseRecord,
    HealthProfile,
    OnboardingDraft,
    PendingBpConfirmation,
)
from haven.tools._helpers import NO_IDENTITY_REPLY, OptionalToolRuntime, degraded, uid_of


@tool(parse_docstring=True)
async def delete_my_data(runtime: OptionalToolRuntime = None) -> DeleteResult:
    """删除当前用户的全部健康数据。

    危险操作 —— 仅在用户明确要求（如「删除我的数据」）且获得
    中断门再次批准后调用。删除 6 张业务表的全部记录，审计日志
    仅追加一条去标识的 deletion 事件——删除本身可追溯，健康数据不可恢复。
    """
    uid = uid_of(runtime)
    if uid is None:
        return DeleteResult(ok=False, message=NO_IDENTITY_REPLY)
    try:
        async with session_scope() as session:
            for model in (
                BloodPressure,
                DiseaseRecord,
                HealthProfile,
                ConsentRecord,
                PendingBpConfirmation,
                OnboardingDraft,
            ):
                await session.execute(delete(model).where(model.user_id == uid))
            await record_audit(
                session,
                subject_key=subject_key_for(uid),
                event_type="deletion",
                event_summary="deletion:executed",
            )
    except DatabaseUnavailable:
        return DeleteResult(ok=False, message=degraded())
    return DeleteResult(ok=True, deleted=True, message=MSG.delete_done)
