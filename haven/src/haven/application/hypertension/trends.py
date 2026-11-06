"""七日趋势 —— 纯确定性统计，绝不 LLM。

统计逻辑与文案从 `src/haven/application/trends.py` 原样复刻；
与 src 的差异：函数不再依赖 SQLAlchemy 会话，改由工具层取数后传入
`records`（含 systolic / diastolic / measured_at 属性的对象即可，
measured_at 用于排序）。
"""

from dataclasses import dataclass, field
from typing import Any

from haven.application.messages import MSG


@dataclass(frozen=True)
class TrendSummary:
    days: int
    record_count: int
    systolic_avg: float | None = None
    diastolic_avg: float | None = None
    systolic_max: int | None = None
    diastolic_max: int | None = None
    systolic_min: int | None = None
    diastolic_min: int | None = None
    normal_rate: float | None = None
    trend_direction: str = "insufficient_data"
    records: list[Any] = field(default_factory=list)


def _calculate_trend(records: list[Any]) -> TrendSummary:
    if not records:
        return TrendSummary(days=0, record_count=0)

    systolic_values = [r.systolic for r in records]
    diastolic_values = [r.diastolic for r in records]

    normal_count = sum(
        1 for s, d in zip(systolic_values, diastolic_values, strict=True) if s < 140 and d < 90
    )

    if len(records) >= 3:
        first_half_s = sum(systolic_values[: len(records) // 2]) / (len(records) // 2)
        second_half_s = sum(systolic_values[len(records) // 2 :]) / (
            len(records) - len(records) // 2
        )
        diff = second_half_s - first_half_s
        if diff > 5:
            direction = "rising"
        elif diff < -5:
            direction = "falling"
        else:
            direction = "stable"
    else:
        direction = "insufficient_data"

    return TrendSummary(
        days=0,
        record_count=len(records),
        systolic_avg=round(sum(systolic_values) / len(systolic_values), 1),
        diastolic_avg=round(sum(diastolic_values) / len(diastolic_values), 1),
        systolic_max=max(systolic_values),
        diastolic_max=max(diastolic_values),
        systolic_min=min(systolic_values),
        diastolic_min=min(diastolic_values),
        normal_rate=round(normal_count / len(records) * 100, 1),
        trend_direction=direction,
        records=records,
    )


def calculate_seven_day_trend(records: list[Any]) -> TrendSummary:
    """对最近 7 天的记录（升序 or 无序均可，函数内排序）计算趋势概要。"""
    records_sorted = sorted(records, key=lambda r: r.measured_at)
    summary = _calculate_trend(records_sorted)
    return TrendSummary(
        days=7,
        record_count=summary.record_count,
        systolic_avg=summary.systolic_avg,
        diastolic_avg=summary.diastolic_avg,
        systolic_max=summary.systolic_max,
        diastolic_max=summary.diastolic_max,
        systolic_min=summary.systolic_min,
        diastolic_min=summary.diastolic_min,
        normal_rate=summary.normal_rate,
        trend_direction=summary.trend_direction,
        records=summary.records,
    )


def format_trend_message(summary: TrendSummary) -> str:
    """兼容原调用入口，文案由统一展示层渲染。"""
    return MSG.format_trend_message(
        summary,
        needs_consultation=summary.normal_rate is not None and summary.normal_rate < 50,
    )
