"""异常血压二次确认逻辑与固定文案。

逻辑从 `src/haven/application/confirmation.py` 原样复刻；
`LEVEL_CN` 中文分级名取自 `src/haven/agent/chat.py`。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from application.hypertension.validation import validate_blood_pressure

LEVEL_CN = {
    "normal": "正常",
    "high_normal": "正常高值",
    "grade_1": "1级高血压",
    "grade_2": "2级高血压",
    "grade_3": "3级高血压",
    "severe": "严重异常",
}


def pending_expired(expires_at: datetime, *, now: datetime | None = None) -> bool:
    """待确认行是否过期。

    SQLite 读回的时间是 naive，需按 UTC 归一化后再比较（PostgreSQL 原样
    aware）。这是该归一化的**唯一真相源** —— 工具侧与记忆注入侧共用，
    各写一份必然有一处漏掉，把过期行当有效行。
    """
    moment = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    return moment < (now or datetime.now(UTC))


@dataclass(frozen=True)
class ConfirmationResult:
    needs_confirmation: bool
    systolic: int
    diastolic: int
    message: str
    confirmed: bool | None = None


def check_abnormal(
    systolic: int,
    diastolic: int,
) -> ConfirmationResult:
    validation = validate_blood_pressure(systolic, diastolic)

    if not validation.is_valid:
        return ConfirmationResult(
            needs_confirmation=False,
            systolic=systolic,
            diastolic=diastolic,
            message="；".join(validation.errors),
        )

    if not validation.needs_confirmation:
        return ConfirmationResult(
            needs_confirmation=False,
            systolic=systolic,
            diastolic=diastolic,
            message="",
        )

    if validation.is_severe:
        message = (
            f"您输入的血压值为 {systolic}/{diastolic} mmHg，"
            f"属于严重异常范围。"
            f"请确认测量是否正确。如确认无误，建议立即就医。"
        )
    else:
        message = (
            f"您输入的血压值为 {systolic}/{diastolic} mmHg，"
            f"这个数值偏高。确认无误吗？"
        )

    return ConfirmationResult(
        needs_confirmation=True,
        systolic=systolic,
        diastolic=diastolic,
        message=message,
    )
