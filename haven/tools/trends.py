"""七日血压趋势工具 —— 取数后交纯统计模块，绝不 LLM。"""

from __future__ import annotations

from langchain.tools import tool

from application.hypertension.trends import calculate_seven_day_trend, format_trend_message
from schemas.tools import TrendResult
from storage.database import DatabaseUnavailable, session_scope
from tools._helpers import (
    NO_IDENTITY_REPLY,
    OptionalToolRuntime,
    degraded,
    seven_day_records,
    uid_of,
)


@tool(parse_docstring=True)
async def get_seven_day_trend(runtime: OptionalToolRuntime = None) -> TrendResult:
    """查看最近 7 天的血压趋势统计。

    计算记录数、收缩压/舒张压均值、最高/最低值、达标率与趋势方向。
    仅呈现确定性统计结果，不做医学诊断或建议。
    """
    uid = uid_of(runtime)
    if uid is None:
        return TrendResult(ok=False, message=NO_IDENTITY_REPLY)
    try:
        async with session_scope() as session:
            records = await seven_day_records(session, uid)
    except DatabaseUnavailable:
        return TrendResult(ok=False, message=degraded())
    summary = calculate_seven_day_trend(records)
    return TrendResult(
        ok=True,
        has_data=summary.record_count > 0,
        record_count=summary.record_count,
        systolic_avg=summary.systolic_avg,
        diastolic_avg=summary.diastolic_avg,
        systolic_max=summary.systolic_max,
        diastolic_max=summary.diastolic_max,
        systolic_min=summary.systolic_min,
        diastolic_min=summary.diastolic_min,
        normal_rate=summary.normal_rate,
        trend_direction=summary.trend_direction,
        message=format_trend_message(summary),
    )
