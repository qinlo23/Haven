"""建档相关固定回复文案。"""

from __future__ import annotations

from haven.application.commands import C_PROFILE

onboarding_cancelled = f"已取消建档。需要时输入 {C_PROFILE} 重新开始。"


def onboarding_done(nickname: str | None, gender: str, birth_date: str, disease_name: str) -> str:
    """建档完成回执。称呼可跳过 —— 跳过了就不提，不显示占位。"""
    who = f"称呼：{nickname}，" if nickname else ""
    return f"建档完成。{who}性别：{gender}，出生：{birth_date}，健康问题：{disease_name}。"


def profile_text(
    nickname: str | None,
    gender: str,
    birth_date: str,
    height_cm: str | None,
    weight_kg: str | None,
    diseases: list[tuple[str, str]],
) -> str:
    """由展示值生成档案，不访问数据库。"""
    lines = ["你的健康档案："]
    if nickname:
        lines.append(f"· 称呼：{nickname}")
    lines += [
        f"· 性别：{gender}",
        f"· 出生日期：{birth_date}",
    ]
    if height_cm is not None:
        lines.append(f"· 身高：{height_cm} 厘米")
    if weight_kg is not None:
        lines.append(f"· 体重：{weight_kg} 公斤")
    for disease_name, diagnosed_date in diseases:
        lines.append(f"· 健康问题：{disease_name}（确诊于 {diagnosed_date}）")
    lines.append(f"如需更新，输入 {C_PROFILE} 重新填写。")
    return "\n".join(lines)


profile_missing = f"你还没有健康档案。输入 {C_PROFILE} 开始建档。"


def onboarding_required_field(label: str) -> str:
    """字段校验失败提示。"""
    return f"「{label}」是必填项，不能跳过。请重新询问这一项。"


def onboarding_unknown_field(step: str, allowed: tuple[str, ...]) -> str:
    """字段校验失败提示。"""
    return f"未知的建档项「{step}」。合法取值：{'、'.join(allowed)}。"


def onboarding_invalid_diagnosed_date(text: str) -> str:
    """字段校验失败提示。"""
    return f"确诊时间无法识别（收到「{text}」，示例 2020-05 或 2020-05-01），请重试。"


def onboarding_future_diagnosed_date(value: str) -> str:
    """字段校验失败提示。"""
    return f"确诊时间不能晚于今天（收到 {value}），请确认后重试。"


def onboarding_long_nickname(limit: int, text: str) -> str:
    """字段校验失败提示。"""
    return f"称呼最多 {limit} 个字，收到「{text}」。请简化后重试。"


def onboarding_invalid_gender(text: str) -> str:
    """字段校验失败提示。"""
    return f"性别只能填写「男」或「女」，收到「{text}」。请确认后重试。"


def onboarding_invalid_birth_date(text: str, minimum: str) -> str:
    """字段校验失败提示。"""
    return f"出生日期需在 {minimum} 到今天的范围内，收到「{text}」。请按 1950-03-12 的格式重试。"


def onboarding_invalid_height(minimum: int, maximum: int, text: str) -> str:
    """字段校验失败提示。"""
    return f"身高需在 {minimum}-{maximum} 厘米之间，收到 {text}。请确认后重试。"


def onboarding_invalid_weight(minimum: int, maximum: int, text: str) -> str:
    """字段校验失败提示。"""
    return f"体重需在 {minimum}-{maximum} 公斤之间，收到 {text}。请确认后重试。"


onboarding_unsupported_disease = "0.0.1 版本仅支持登记高血压。"
