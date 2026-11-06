"""血压记录相关固定回复文案。"""

from __future__ import annotations

from haven.application.commands import C_CANCEL, C_CONFIRM

bp_invalid = "血压数值不合法，请重新输入"
bp_cancelled = "好的，这条没有保存。请重新测量后再告诉我数值。"
bp_need_value = "请告诉我你的血压值，例如：120/80"


def bp_recorded(systolic: int, diastolic: int, level: str) -> str:
    return f"已记录血压 {systolic}/{diastolic} mmHg（{level}）。"


def bp_confirmed(systolic: int, diastolic: int) -> str:
    return f"已为你记录血压 {systolic}/{diastolic} mmHg。"


bp_confirm_guidance = (
    f"输入 {C_CONFIRM} 继续保存这条记录；输入 {C_CANCEL} 不保存；"
    "若测量有误或想重新测量，直接告诉我新的血压数值即可。"
)
bp_parse_time_error = "测量时间格式无法识别（如 2026-09-01T08:00:00），这条未保存。"
bp_future_time_error = "测量时间不能晚于当前时间，这条未保存。请确认时间后重新输入。"
bp_confirm_miss = "未找到可确认的血压记录（记录可能已超过 24 小时或数值不一致），未保存。请重新测量后告诉我最新数值。"
bp_no_pending = "当前没有待确认的血压记录。\n" + bp_need_value


def bp_duplicate(systolic: int, diastolic: int) -> str:
    """重复记录回执。"""
    return f"{systolic}/{diastolic} mmHg 这条在 1 分钟内已记录过，未重复保存。"


def bp_invalid_systolic(minimum: int, maximum: int, systolic: int) -> str:
    """字段校验失败提示。"""
    return f"收缩压必须在 {minimum}-{maximum} mmHg 之间，收到 {systolic}"


def bp_invalid_diastolic(minimum: int, maximum: int, diastolic: int) -> str:
    """字段校验失败提示。"""
    return f"舒张压必须在 {minimum}-{maximum} mmHg 之间，收到 {diastolic}"


def bp_invalid_order(systolic: int, diastolic: int) -> str:
    """字段校验失败提示。"""
    return f"收缩压 ({systolic}) 必须大于舒张压 ({diastolic})"
