"""工具返回值结构化输出 —— 供 `tools/` 下各工具函数使用。

每个工具返回一个 Pydantic 模型，同时包含：
- 人类可读的 message 字段（模型可直接用于回复用户，等价于原来的 str 返回值）
- 结构化数据字段（供模型推理逻辑或下游消费方直接解析）

返回值写入 ToolMessage 时由 LangChain 自动序列化（通常为 JSON），
现代 LLM 可同时理解结构化数据与自然语言。

=============================  消费者映射  =============================
| 模型                            | 消费者（工具函数）                       |
|--------------------------------|---------------------------------------|
| BpRecordResult                 | tools/vitals.py : record_blood_pressure |
| BpPendingResult                | tools/vitals.py : get_pending_blood_pressure |
| BpConfirmResult                | tools/vitals.py : confirm_abnormal_blood_pressure |
| TrendResult                    | tools/trends.py : get_seven_day_trend   |
| OnboardingProfileResult        | tools/onboarding.py : get_health_profile |
| OnboardingSaveResult           | tools/onboarding.py : save_health_profile |
| OnboardingDraftResult          | tools/onboarding.py : save_onboarding_draft |
| ConsentPolicyResult            | tools/consent.py : get_consent_policy    |
| ConsentStatusResult            | tools/consent.py : consent_status        |
| ConsentRecordResult            | tools/consent.py : record_consent        |
| DeleteResult                   | tools/account.py : delete_my_data        |
======================================================================
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ══════════════════════════════════════════════════════════════════════
# tools/vitals.py
# ══════════════════════════════════════════════════════════════════════

class BpRecordResult(BaseModel):
    """`record_blood_pressure` 的返回值。

    消费者: tools/vitals.py : record_blood_pressure()
    """

    ok: bool = Field(description="操作是否成功（false=参数错误/权限不足/数据库不可用）")
    systolic: int = Field(default=0, description="收缩压（高压），mmHg；ok=False 时为 0")
    diastolic: int = Field(default=0, description="舒张压（低压），mmHg；ok=False 时为 0")
    level: str = Field(default="", description="血压分级中文名，如「正常」「1 级高血压」")
    needs_confirmation: bool = Field(
        default=False,
        description="是否需要用户二次确认（二级及以上异常值）",
    )
    confirmed: bool = Field(
        default=False,
        description="是否已直接入库（false=仅写入待确认行，需 confirm_abnormal_blood_pressure）",
    )
    message: str = Field(description="人类可读的回复文本，模型可直接用于回复用户")


class BpPendingResult(BaseModel):
    """`get_pending_blood_pressure` 的返回值。

    消费者: tools/vitals.py : get_pending_blood_pressure()
    """

    ok: bool = Field(description="操作是否成功")
    has_pending: bool = Field(default=False, description="是否存在待确认的异常血压记录")
    systolic: int | None = Field(default=None, description="待确认的收缩压")
    diastolic: int | None = Field(default=None, description="待确认的舒张压")
    message: str = Field(description="人类可读的回复文本")


class BpConfirmResult(BaseModel):
    """`confirm_abnormal_blood_pressure` 的返回值。

    消费者: tools/vitals.py : confirm_abnormal_blood_pressure()
    """

    ok: bool = Field(description="操作是否成功")
    systolic: int = Field(default=0, description="已确认入库的收缩压；ok=False 时为 0")
    diastolic: int = Field(default=0, description="已确认入库的舒张压；ok=False 时为 0")
    confirmed: bool = Field(default=False, description="是否成功确认并入库")
    message: str = Field(description="人类可读的回复文本")


# ══════════════════════════════════════════════════════════════════════
# tools/trends.py
# ══════════════════════════════════════════════════════════════════════

class TrendResult(BaseModel):
    """`get_seven_day_trend` 的返回值。

    消费者: tools/trends.py : get_seven_day_trend()
    """

    ok: bool = Field(description="操作是否成功")
    has_data: bool = Field(default=False, description="近 7 天是否有血压记录")
    record_count: int = Field(default=0, description="记录条数")
    systolic_avg: float | None = Field(default=None, description="收缩压均值")
    diastolic_avg: float | None = Field(default=None, description="舒张压均值")
    systolic_max: int | None = Field(default=None, description="收缩压最大值")
    diastolic_max: int | None = Field(default=None, description="舒张压最大值")
    systolic_min: int | None = Field(default=None, description="收缩压最小值")
    diastolic_min: int | None = Field(default=None, description="舒张压最小值")
    normal_rate: float | None = Field(default=None, description="达标率（%），收缩压<140 且 舒张压<90")
    trend_direction: str = Field(
        default="insufficient_data",
        description="趋势方向：rising / falling / stable / insufficient_data",
    )
    message: str = Field(description="人类可读的趋势摘要文本")


# ══════════════════════════════════════════════════════════════════════
# tools/onboarding.py
# ══════════════════════════════════════════════════════════════════════

class OnboardingProfileResult(BaseModel):
    """`get_health_profile` 的返回值。

    消费者: tools/onboarding.py : get_health_profile()
    """

    ok: bool = Field(description="操作是否成功")
    has_profile: bool = Field(default=False, description="用户是否已有健康档案")
    nickname: str | None = Field(default=None, description="称呼")
    gender: str | None = Field(default=None, description="性别")
    birth_date: str | None = Field(default=None, description="出生日期 ISO 格式")
    height_cm: float | None = Field(default=None, description="身高（厘米）")
    weight_kg: float | None = Field(default=None, description="体重（公斤）")
    diseases: list[str] = Field(default_factory=list, description="登记的健康问题名称列表")
    message: str = Field(description="人类可读的档案展示文本")


class OnboardingSaveResult(BaseModel):
    """`save_health_profile` 的返回值。

    消费者: tools/onboarding.py : save_health_profile()
    """

    ok: bool = Field(description="操作是否成功")
    nickname: str | None = Field(default=None, description="保存的称呼")
    gender: str | None = Field(default=None, description="保存的性别；失败时为空")
    birth_date: str | None = Field(default=None, description="保存的出生日期 ISO 格式；失败时为空")
    disease_name: str | None = Field(default=None, description="保存的健康问题名称；失败时为空")
    saved: bool = Field(default=False, description="是否成功保存")
    message: str = Field(description="人类可读的确认文本")


class OnboardingDraftResult(BaseModel):
    """`save_onboarding_draft` 的返回值。

    消费者: tools/onboarding.py : save_onboarding_draft()
    """

    ok: bool = Field(description="操作是否成功")
    step: str = Field(default="", description="当前保存的建档步骤名")
    next_step: str | None = Field(default=None, description="下一步需要询问的字段名")
    completed: bool = Field(default=False, description="建档是否已完成所有步骤")
    message: str = Field(description="内部记账结果（模型不应直接转达给用户）")


# ══════════════════════════════════════════════════════════════════════
# tools/consent.py
# ══════════════════════════════════════════════════════════════════════

class ConsentPolicyResult(BaseModel):
    """`get_consent_policy` 的返回值。

    消费者: tools/consent.py : get_consent_policy()
    """

    policy_version: str = Field(description="隐私政策版本号")
    message: str = Field(description="隐私政策全文 + 同意指引")


class ConsentStatusResult(BaseModel):
    """`consent_status` 的返回值。

    消费者: tools/consent.py : consent_status()
    """

    ok: bool = Field(description="操作是否成功")
    consented: bool = Field(default=False, description="用户是否已同意隐私政策")
    policy_version: str | None = Field(default=None, description="已同意的版本号")
    message: str = Field(description="人类可读的状态文本")


class ConsentRecordResult(BaseModel):
    """`record_consent` 的返回值。

    消费者: tools/consent.py : record_consent()
    """

    ok: bool = Field(description="操作是否成功")
    consented: bool = Field(default=False, description="同意是否成功记录（幂等：已有则为 True）")
    policy_version: str | None = Field(default=None, description="记录的政策版本号；失败时为空")
    message: str = Field(description="人类可读的确认文本")


# ══════════════════════════════════════════════════════════════════════
# tools/account.py
# ══════════════════════════════════════════════════════════════════════

class DeleteResult(BaseModel):
    """`delete_my_data` 的返回值。

    消费者: tools/account.py : delete_my_data()
    """

    ok: bool = Field(description="操作是否成功")
    deleted: bool = Field(default=False, description="是否成功删除")
    message: str = Field(description="人类可读的确认文本")
