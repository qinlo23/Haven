"""血压记录工具 —— 确定性校验 + 异常二次确认的两阶段写入。

流程：`record_blood_pressure` 校验并（若需确认）只落待确认行，不写血压表；
用户改报新数值即视为放弃旧读数（记录新值时一并清掉旧待确认行）；
`confirm_abnormal_blood_pressure` 校验匹配后消费待确认行并正式入库。
LLM 无法绕开确认：二级及以上异常值没有「确认」就没有行。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain.tools import tool
from sqlalchemy import select

from haven.application.hypertension.confirmation import LEVEL_CN, check_abnormal, pending_expired
from haven.application.hypertension.validation import ValidationLevel, validate_blood_pressure
from haven.application.messages import MSG
from haven.safety.disclaimers import DisclaimerType, get_disclaimer
from haven.schemas.tools import BpConfirmResult, BpPendingResult, BpRecordResult
from haven.storage.database import DatabaseUnavailable, session_scope
from haven.storage.models import PENDING_CONFIRM_TTL, BloodPressure, PendingBpConfirmation
from haven.tools._helpers import (
    NO_IDENTITY_REPLY,
    OptionalToolRuntime,
    degraded,
    is_future,
    parse_measured_at,
    uid_of,
)

#: 重复记录窗口（与 src 一致）。
DUPLICATE_WINDOW = timedelta(seconds=60)

_LEVEL_ORDER = list(ValidationLevel)


def _worst_level(
    systolic_level: ValidationLevel, diastolic_level: ValidationLevel
) -> ValidationLevel:
    """显示用分级取两者中更严重者（枚举定义顺序即严重度升序）。"""
    if _LEVEL_ORDER.index(systolic_level) >= _LEVEL_ORDER.index(diastolic_level):
        return systolic_level
    return diastolic_level


async def _find_pending(session, uid: str) -> PendingBpConfirmation | None:
    stmt = select(PendingBpConfirmation).where(PendingBpConfirmation.user_id == uid).limit(1)
    return (await session.execute(stmt)).scalars().first()


async def _recent_duplicate(
    session, uid: str, systolic: int, diastolic: int, measured_at: datetime
) -> bool:
    """同一数值在同一时间窗口（±60 秒）内已有正式记录。"""
    stmt = (
        select(BloodPressure)
        .where(
            BloodPressure.user_id == uid,
            BloodPressure.systolic == systolic,
            BloodPressure.diastolic == diastolic,
            BloodPressure.measured_at >= measured_at - DUPLICATE_WINDOW,
            BloodPressure.measured_at <= measured_at + DUPLICATE_WINDOW,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first() is not None


@tool(parse_docstring=True)
async def record_blood_pressure(
    systolic: int | None = None,
    diastolic: int | None = None,
    measured_at: str | None = None,
    runtime: OptionalToolRuntime = None,
) -> BpRecordResult:
    """记录一次手动血压测量。

    数值先经过确定性血压分级校验（正常/正常高值/1-3级高血压）。
    二级及以上异常值不会直接保存，转入两阶段确认流程，
    需用户通过 confirm_abnormal_blood_pressure 确认后才正式入库。
    正常血压值直接入库。

    Args:
        systolic: 收缩压（高压），单位 mmHg，范围 60-300。
        diastolic: 舒张压（低压），单位 mmHg，范围 40-200。
        measured_at: 测量时间，ISO 8601 格式（如 "2026-09-01T08:00"）。
            留空则默认当前时间。不能晚于当前时间。
    """
    uid = uid_of(runtime)
    if uid is None:
        return BpRecordResult(ok=False, message=NO_IDENTITY_REPLY)
    if systolic is None or diastolic is None:
        return BpRecordResult(ok=False, message=MSG.bp_need_value)

    measured = parse_measured_at(measured_at)
    if measured is None:
        return BpRecordResult(ok=False, message=MSG.bp_parse_time_error)
    if is_future(measured):
        return BpRecordResult(ok=False, message=MSG.bp_future_time_error)

    validation = validate_blood_pressure(systolic, diastolic)
    if not validation.is_valid:
        return BpRecordResult(
            ok=False,
            systolic=systolic,
            diastolic=diastolic,
            message=f"{MSG.bp_invalid}：{'；'.join(validation.errors)}。",
        )
    confirm = check_abnormal(systolic, diastolic)

    try:
        async with session_scope() as session:
            if await _recent_duplicate(session, uid, systolic, diastolic, measured):
                return BpRecordResult(
                    ok=False,
                    systolic=systolic,
                    diastolic=diastolic,
                    message=MSG.bp_duplicate(systolic, diastolic),
                )

            if confirm.needs_confirmation:
                # 两阶段：不落正式记录，只写/刷新待确认行（至多一行）。
                pending = await _find_pending(session, uid)
                now = datetime.now(UTC)
                if pending is None:
                    session.add(
                        PendingBpConfirmation(
                            user_id=uid,
                            systolic=systolic,
                            diastolic=diastolic,
                            measured_at=measured,
                            prompt_text=confirm.message,
                            expires_at=now + PENDING_CONFIRM_TTL,
                        )
                    )
                else:
                    pending.systolic = systolic
                    pending.diastolic = diastolic
                    pending.measured_at = measured
                    pending.prompt_text = confirm.message
                    pending.expires_at = now + PENDING_CONFIRM_TTL
            else:
                # 改报无需确认的新数值 = 放弃上一条待确认读数：清掉旧行，
                # 防止 24 小时内一句「确认」把用户已放弃的旧异常值入库。
                stale = await _find_pending(session, uid)
                if stale is not None:
                    await session.delete(stale)
                session.add(
                    BloodPressure(
                        user_id=uid,
                        systolic=systolic,
                        diastolic=diastolic,
                        measured_at=measured,
                        source="Manual",
                        is_abnormal=validation.is_abnormal,
                        notes=None,
                    )
                )
    except DatabaseUnavailable:
        return BpRecordResult(ok=False, message=degraded())

    if confirm.needs_confirmation:
        message = f"{confirm.message}\n{MSG.bp_confirm_guidance}"
        return BpRecordResult(
            ok=True,
            systolic=systolic,
            diastolic=diastolic,
            level=_worst_level(validation.systolic_level, validation.diastolic_level).value,
            needs_confirmation=True,
            confirmed=False,
            message=message,
        )

    level_cn = LEVEL_CN[_worst_level(validation.systolic_level, validation.diastolic_level).value]
    reply = MSG.bp_recorded(systolic, diastolic, level_cn)
    if validation.is_abnormal:
        reply += "\n\n" + get_disclaimer(DisclaimerType.MEDICAL_ADVICE)
    return BpRecordResult(
        ok=True,
        systolic=systolic,
        diastolic=diastolic,
        level=level_cn,
        needs_confirmation=False,
        confirmed=True,
        message=reply,
    )


@tool(parse_docstring=True)
async def get_pending_blood_pressure(
    runtime: OptionalToolRuntime = None,
) -> BpPendingResult:
    """查询待确认的异常血压记录。

    中断后可通过此工具恢复流程，检查是否有等待用户确认的异常血压。
    记录 24 小时内有效，过期自动清空。
    """
    uid = uid_of(runtime)
    if uid is None:
        return BpPendingResult(ok=False, message=NO_IDENTITY_REPLY)
    try:
        async with session_scope() as session:
            pending = await _find_pending(session, uid)
            if pending is None:
                return BpPendingResult(ok=True, has_pending=False, message=MSG.bp_no_pending)
            if pending_expired(pending.expires_at):
                await session.delete(pending)
                return BpPendingResult(ok=True, has_pending=False, message=MSG.bp_no_pending)
            return BpPendingResult(
                ok=True,
                has_pending=True,
                systolic=pending.systolic,
                diastolic=pending.diastolic,
                message=f"{pending.prompt_text}\n{MSG.bp_confirm_guidance}",
            )
    except DatabaseUnavailable:
        return BpPendingResult(ok=False, message=degraded())


@tool(parse_docstring=True)
async def confirm_abnormal_blood_pressure(
    systolic: int | None = None,
    diastolic: int | None = None,
    runtime: OptionalToolRuntime = None,
) -> BpConfirmResult:
    """确认保存一条异常血压记录。

    仅在用户明确表示「确认」后调用。系统会校验：
    待确认行存在、未过期（24 小时）且数值完全一致时才正式入库；
    任一条件不满足则拒绝，不保存任何数据。

    Args:
        systolic: 收缩压（高压），单位 mmHg。必须与待确认记录完全一致。
        diastolic: 舒张压（低压），单位 mmHg。必须与待确认记录完全一致。
    """
    uid = uid_of(runtime)
    if uid is None:
        return BpConfirmResult(ok=False, message=NO_IDENTITY_REPLY)
    if systolic is None or diastolic is None:
        return BpConfirmResult(ok=False, message=MSG.bp_confirm_miss)

    validation = validate_blood_pressure(systolic, diastolic)
    try:
        async with session_scope() as session:
            pending = await _find_pending(session, uid)
            if pending is None:
                return BpConfirmResult(
                    ok=False, systolic=systolic, diastolic=diastolic, message=MSG.bp_confirm_miss
                )
            if pending_expired(pending.expires_at):
                await session.delete(pending)
                return BpConfirmResult(
                    ok=False, systolic=systolic, diastolic=diastolic, message=MSG.bp_confirm_miss
                )
            if not (pending.systolic == systolic and pending.diastolic == diastolic):
                return BpConfirmResult(
                    ok=False, systolic=systolic, diastolic=diastolic, message=MSG.bp_confirm_miss
                )

            await session.delete(pending)
            session.add(
                BloodPressure(
                    user_id=uid,
                    systolic=systolic,
                    diastolic=diastolic,
                    measured_at=pending.measured_at,
                    source="Manual",
                    is_abnormal=validation.is_abnormal,
                    notes=None,
                )
            )
    except DatabaseUnavailable:
        return BpConfirmResult(ok=False, message=degraded())

    reply = MSG.bp_confirmed(systolic, diastolic)
    if validation.is_severe:
        reply += "\n\n该数值属于严重异常范围。如已确认测量无误，请尽快就医。"
    reply += "\n\n" + get_disclaimer(DisclaimerType.MEDICAL_ADVICE)
    return BpConfirmResult(
        ok=True,
        systolic=systolic,
        diastolic=diastolic,
        confirmed=True,
        message=reply,
    )
