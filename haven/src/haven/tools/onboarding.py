"""健康建档工具（0.0.1：高血压）。

字段词表、状态机与逐字段校验集中在 `application/onboarding.py` ——
**建档草稿（保存中）与正式建档（保存时）共用同一套规则**，不会两处判出
不同结果。建档即整档重写：删除旧健康问题记录后写一条当前记录
（避免重复行累积）。

续接（B3）：用户每答一项，模型调 `save_onboarding_draft` 落一行草稿
（`onboarding_drafts`，user_id 主键），换线程/换会话后由记忆注入读出，
从 `next_field` 接着问，不必重头再来。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from langchain.tools import tool
from sqlalchemy import delete, select

from haven.application.messages import MSG
from haven.application.onboarding import (
    STEP_LABELS,
    STEP_ORDER,
    SUPPORTED_DISEASE,
    clean_value,
    fmt_num,
    next_step,
    validate_step,
)
from haven.schemas.tools import OnboardingDraftResult, OnboardingProfileResult, OnboardingSaveResult
from haven.storage.database import DatabaseUnavailable, session_scope
from haven.storage.models import DiseaseRecord, HealthProfile, OnboardingDraft
from haven.tools._helpers import NO_IDENTITY_REPLY, OptionalToolRuntime, degraded, uid_of


@tool(parse_docstring=True)
async def get_health_profile(runtime: OptionalToolRuntime = None) -> OnboardingProfileResult:
    """读取当前用户的健康档案。

    返回已保存的健康信息（昵称、性别、出生日期、身高、体重）及登记的健康问题。
    需先同意隐私政策。
    """
    uid = uid_of(runtime)
    if uid is None:
        return OnboardingProfileResult(ok=False, message=NO_IDENTITY_REPLY)
    try:
        async with session_scope() as session:
            stmt = select(HealthProfile).where(HealthProfile.user_id == uid).limit(1)
            profile = (await session.execute(stmt)).scalars().first()
            if profile is None:
                return OnboardingProfileResult(
                    ok=True,
                    has_profile=False,
                    message=MSG.profile_missing,
                )
            diseases = (
                (await session.execute(select(DiseaseRecord).where(DiseaseRecord.user_id == uid)))
                .scalars()
                .all()
            )
    except DatabaseUnavailable:
        return OnboardingProfileResult(ok=False, message=degraded())

    disease_names = [d.disease_name for d in diseases]

    return OnboardingProfileResult(
        ok=True,
        has_profile=True,
        nickname=profile.nickname,
        gender=profile.gender,
        birth_date=profile.birth_date.isoformat(),
        height_cm=profile.height_cm,
        weight_kg=profile.weight_kg,
        diseases=disease_names,
        message=MSG.profile_text(
            profile.nickname,
            profile.gender,
            profile.birth_date.isoformat(),
            fmt_num(profile.height_cm) if profile.height_cm is not None else None,
            fmt_num(profile.weight_kg) if profile.weight_kg is not None else None,
            [(d.disease_name, d.diagnosed_date.isoformat()) for d in diseases],
        ),
    )


@tool(parse_docstring=True)
async def save_health_profile(
    nickname: str | None = None,
    gender: str | None = None,
    birth_date: str | None = None,
    height_cm: float | None = None,
    weight_kg: float | None = None,
    disease_name: str = SUPPORTED_DISEASE,
    diagnosed_date: str | None = None,
    runtime: OptionalToolRuntime = None,
) -> OnboardingSaveResult:
    """保存（或更新）健康档案。

    须先获得用户同意的隐私政策，且用户已确认建档摘要后调用。
    0.0.1 仅支持登记高血压。成功时在同一事务内删除建档草稿。
    整档重写语义：省略可跳过项等于清空它，不做「不传即不改」的隐藏规则。

    Args:
        nickname: 用户称呼，自由文本（可为空）。
        gender: 性别，合法取值「男」「女」「其他」。
        birth_date: 出生日期，格式 YYYY-MM-DD（如 "1990-01-15"）。
        height_cm: 身高（厘米），范围 40-300，可跳过。
        weight_kg: 体重（公斤），范围 1-700，可跳过。
        disease_name: 疾病名称。当前固定为「高血压」。
        diagnosed_date: 确诊日期，格式 YYYY-MM-DD，可跳过（默认今天）。
    """
    uid = uid_of(runtime)
    if uid is None:
        return OnboardingSaveResult(ok=False, message=NO_IDENTITY_REPLY)

    # ── 确定性校验（与建档草稿共用同一套规则，不依赖 LLM） ──
    # 校验通过即保证：gender/birth/disease 非 None；其余可跳过项可为 None。
    name, error = validate_step("nickname", nickname)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    gender_value, error = validate_step("gender", gender)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    birth, error = validate_step("birth_date", birth_date)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    height, error = validate_step("height_cm", height_cm)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    weight, error = validate_step("weight_kg", weight_kg)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    disease, error = validate_step("disease_name", disease_name or SUPPORTED_DISEASE)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    diagnosed, error = validate_step("diagnosed_date", diagnosed_date)
    if error is not None:
        return OnboardingSaveResult(ok=False, message=error)
    if diagnosed is None:  # 可跳过项：默认今天
        diagnosed = date.today()

    # validate_step 按字段返回不同类型；写库前显式收窄，异常形状安全退出。
    if name is not None and not isinstance(name, str):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)
    if not isinstance(gender_value, str):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)
    if not isinstance(birth, date):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)
    if height is not None and not isinstance(height, float):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)
    if weight is not None and not isinstance(weight, float):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)
    if not isinstance(disease, str):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)
    if not isinstance(diagnosed, date):
        return OnboardingSaveResult(ok=False, message=MSG.error_fallback)

    try:
        async with session_scope() as session:
            stmt = select(HealthProfile).where(HealthProfile.user_id == uid).limit(1)
            profile = (await session.execute(stmt)).scalars().first()
            if profile is None:
                session.add(
                    HealthProfile(
                        user_id=uid,
                        nickname=name,
                        gender=gender_value,
                        birth_date=birth,
                        height_cm=height,
                        weight_kg=weight,
                    )
                )
            else:
                profile.nickname = name
                profile.gender = gender_value
                profile.birth_date = birth
                profile.height_cm = height
                profile.weight_kg = weight
                profile.updated_at = datetime.now(UTC)
            # 0.0.1 单一健康问题：整档重写，删除旧记录避免重复行。
            await session.execute(delete(DiseaseRecord).where(DiseaseRecord.user_id == uid))
            session.add(
                DiseaseRecord(
                    user_id=uid,
                    disease_name=disease,
                    diagnosed_date=diagnosed,
                    severity=None,
                    status="Active",
                    notes=None,
                )
            )
            # 档案落定，草稿使命结束（同一事务内）
            await session.execute(delete(OnboardingDraft).where(OnboardingDraft.user_id == uid))
    except DatabaseUnavailable:
        return OnboardingSaveResult(ok=False, message=degraded())
    return OnboardingSaveResult(
        ok=True,
        nickname=name,
        gender=gender_value,
        birth_date=birth.isoformat(),
        disease_name=disease,
        saved=True,
        message=MSG.onboarding_done(name, gender_value, birth.isoformat(), disease),
    )


@tool(parse_docstring=True)
async def save_onboarding_draft(
    step: str | None = None,
    value: str | None = None,
    runtime: OptionalToolRuntime = None,
) -> OnboardingDraftResult:
    """记录建档进度（内部记账）。

    工具返回值不向用户转达——仅用于推进跨会话续接的建档状态机（B3）。
    用户每提供一项后由模型调用，使建档成为可跨线程/跨会话续接的对话状态流。

    Args:
        step: 建档字段名，封闭词表。合法取值：nickname, gender, birth_date,
            height_cm, weight_kg, disease_name, diagnosed_date。
            模型不能自由发挥，否则 next_field 游标会失效。
        value: 该字段的用户输入值。留空表示跳过，仅对可跳过项合法。
    """
    uid = uid_of(runtime)
    if uid is None:
        return OnboardingDraftResult(ok=False, step="", message=NO_IDENTITY_REPLY)
    if step not in STEP_ORDER:
        return OnboardingDraftResult(
            ok=False,
            step=step or "",
            message=(
                f"（内部）未知的建档项「{clean_value(step)}」。合法取值：{'、'.join(STEP_ORDER)}。"
            ),
        )

    parsed, error = validate_step(step, value)
    if error is not None:
        return OnboardingDraftResult(ok=False, step=step or "", message=error)

    try:
        async with session_scope() as session:
            stmt = select(OnboardingDraft).where(OnboardingDraft.user_id == uid).limit(1)
            draft = (await session.execute(stmt)).scalars().first()
            if draft is None:
                # 同一用户的建档是逐项问答，天然串行；并发同 step 的 PK 冲突
                # 概率可忽略，且失败只会降级为一次固定文案，不丢已有进度。
                draft = OnboardingDraft(user_id=uid)
                session.add(draft)
            setattr(draft, step, parsed)  # parsed 为 None 即"该项跳过"
            draft.next_field = next_step(step)
            draft.updated_at = datetime.now(UTC)
    except DatabaseUnavailable:
        return OnboardingDraftResult(ok=False, step=step or "", message=degraded())

    upcoming = next_step(step)
    tail = (
        f"下一项：{STEP_LABELS[upcoming]}"
        if upcoming is not None
        else f"{len(STEP_ORDER)} 项已收集齐，等待用户确认"
    )
    return OnboardingDraftResult(
        ok=True,
        step=step or "",
        next_step=upcoming,
        completed=upcoming is None,
        message=f"（内部记录，不要向用户转达本条）{STEP_LABELS[step]} 进度已保存；{tail}。",
    )
