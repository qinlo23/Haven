"""血压校验与分级 —— 权威表（0.0.1 基线，勿再引入 src 中与 /api/blood-pressure
路由冲突的第二张表）。

值从 `src/haven/application/validation.py` 原样复刻。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from application.messages import MSG


class ValidationLevel(Enum):
    NORMAL = "normal"
    HIGH_NORMAL = "high_normal"
    GRADE_1 = "grade_1"
    GRADE_2 = "grade_2"
    GRADE_3 = "grade_3"
    SEVERE = "severe"


@dataclass(frozen=True)
class ValidationResult:
    is_valid: bool
    systolic: int
    diastolic: int
    systolic_level: ValidationLevel = ValidationLevel.NORMAL
    diastolic_level: ValidationLevel = ValidationLevel.NORMAL
    errors: list[str] = field(default_factory=list)

    @property
    def is_abnormal(self) -> bool:
        return self.systolic_level not in (
            ValidationLevel.NORMAL,
            ValidationLevel.HIGH_NORMAL,
        ) or self.diastolic_level not in (
            ValidationLevel.NORMAL,
            ValidationLevel.HIGH_NORMAL,
        )

    @property
    def is_severe(self) -> bool:
        return (
            self.systolic_level == ValidationLevel.SEVERE
            or self.diastolic_level == ValidationLevel.SEVERE
        )

    @property
    def needs_confirmation(self) -> bool:
        return self.systolic_level in (
            ValidationLevel.GRADE_2,
            ValidationLevel.GRADE_3,
            ValidationLevel.SEVERE,
        ) or self.diastolic_level in (
            ValidationLevel.GRADE_2,
            ValidationLevel.GRADE_3,
            ValidationLevel.SEVERE,
        )


SYSTOLIC_MIN = 60
SYSTOLIC_MAX = 300
DIASTOLIC_MIN = 30
DIASTOLIC_MAX = 200


def classify_systolic(value: int) -> ValidationLevel:
    if value < 120:
        return ValidationLevel.NORMAL
    if value < 140:
        return ValidationLevel.HIGH_NORMAL
    if value < 160:
        return ValidationLevel.GRADE_1
    if value < 180:
        return ValidationLevel.GRADE_2
    if value < 220:
        return ValidationLevel.GRADE_3
    return ValidationLevel.SEVERE


def classify_diastolic(value: int) -> ValidationLevel:
    if value < 80:
        return ValidationLevel.NORMAL
    if value < 90:
        return ValidationLevel.HIGH_NORMAL
    if value < 100:
        return ValidationLevel.GRADE_1
    if value < 110:
        return ValidationLevel.GRADE_2
    if value < 130:
        return ValidationLevel.GRADE_3
    return ValidationLevel.SEVERE


def validate_blood_pressure(
    systolic: int,
    diastolic: int,
) -> ValidationResult:
    errors: list[str] = []

    if systolic < SYSTOLIC_MIN or systolic > SYSTOLIC_MAX:
        errors.append(MSG.bp_invalid_systolic(SYSTOLIC_MIN, SYSTOLIC_MAX, systolic))

    if diastolic < DIASTOLIC_MIN or diastolic > DIASTOLIC_MAX:
        errors.append(MSG.bp_invalid_diastolic(DIASTOLIC_MIN, DIASTOLIC_MAX, diastolic))

    if systolic <= diastolic:
        errors.append(MSG.bp_invalid_order(systolic, diastolic))

    if errors:
        return ValidationResult(
            is_valid=False,
            systolic=systolic,
            diastolic=diastolic,
            errors=errors,
        )

    return ValidationResult(
        is_valid=True,
        systolic=systolic,
        diastolic=diastolic,
        systolic_level=classify_systolic(systolic),
        diastolic_level=classify_diastolic(diastolic),
    )
