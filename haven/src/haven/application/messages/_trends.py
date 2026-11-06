"""血压趋势的固定展示格式；不负责统计与判定。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from application.hypertension.trends import TrendSummary


def format_trend_message(summary: TrendSummary, *, needs_consultation: bool = False) -> str:
    if summary.record_count == 0:
        return "最近 7 天没有血压记录，请先记录你的血压数据。"

    if summary.record_count < 3:
        return (
            f"最近 7 天仅有 {summary.record_count} 条血压记录，"
            f"数据不足以分析趋势。建议每天早晚各测量一次。"
        )

    direction_text = {
        "rising": "呈上升趋势，请关注",
        "falling": "呈下降趋势",
        "stable": "保持稳定",
        "insufficient_data": "趋势尚不明确",
    }

    msg = (
        f"最近 7 天共 {summary.record_count} 条血压记录：\n"
        f"• 平均血压：{summary.systolic_avg}/{summary.diastolic_avg} mmHg\n"
        f"• 最高血压：{summary.systolic_max}/{summary.diastolic_max} mmHg\n"
        f"• 最低血压：{summary.systolic_min}/{summary.diastolic_min} mmHg\n"
        f"• 达标率：{summary.normal_rate}%\n"
        f"• 趋势：{direction_text.get(summary.trend_direction, summary.trend_direction)}"
    )

    if needs_consultation:
        msg += "\n\n你的血压达标率偏低，建议咨询医生调整治疗方案。"

    return msg
