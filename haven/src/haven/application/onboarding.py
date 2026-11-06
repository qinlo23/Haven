"""建档字段的词表、状态机与逐字段校验 —— 纯确定性，绝不 LLM。

**封闭词表**：`STEP_ORDER` 的字段名与 `save_health_profile` 的参数名逐一对应，
建档草稿工具只接受这六个取值，模型不能自由发挥（B3「可续接」依赖这个不变量）。

字段常量与日期解析原先散在 `tools/onboarding.py` / `tools/_helpers.py`，
现集中到本模块：**建档草稿（保存中）与正式建档（保存时）共用同一套校验**，
避免两处各判一次、判出不同结果。错误文案与 `save_health_profile` 原有口径一致。
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from haven.application.messages import MSG

#: 建档顺序 —— 即状态机游标（`OnboardingDraft.next_field`）的取值域。
#: 称呼排第一：最早问到，之后每轮对话都叫得出名字。
#: **任何显示「已收集齐」的文案都必须由 `len(STEP_ORDER)` 推导**，别再写死数目。
STEP_ORDER: tuple[str, ...] = (
    "nickname",
    "gender",
    "birth_date",
    "height_cm",
    "weight_kg",
    "disease_name",
    "diagnosed_date",
)

#: 可跳过项 —— 与 instructions.md §五 的「可跳过」标记一致。
SKIPPABLE: frozenset[str] = frozenset({"nickname", "height_cm", "weight_kg", "diagnosed_date"})

STEP_LABELS: dict[str, str] = {
    "nickname": "称呼",
    "gender": "性别",
    "birth_date": "出生日期",
    "height_cm": "身高",
    "weight_kg": "体重",
    "disease_name": "健康问题",
    "diagnosed_date": "确诊时间",
}

GENDERS = ("男", "女")
BIRTH_MIN = date(1900, 1, 1)
HEIGHT_MIN, HEIGHT_MAX = 80, 250
WEIGHT_MIN, WEIGHT_MAX = 2, 500
SUPPORTED_DISEASE = "高血压"

#: 单字段入库前的长度上限 —— 字段值最终会被注入 system prompt，
#: 限长 + 去控制字符是为了让用户可控的文本撑不破记忆块。
MAX_FIELD_CHARS = 64

#: 称呼的长度上限。比 `MAX_FIELD_CHARS` 严 —— 再长的"名字"不是名字。
#: 超长**拒绝**而非静默截断：把人的名字悄悄切掉，比让他重说一次更糟。
NICKNAME_MAX_CHARS = 20

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def next_step(step: str) -> str | None:
    """下一步的字段名；已是最后一步返回 None。"""
    index = STEP_ORDER.index(step)
    if index + 1 < len(STEP_ORDER):
        return STEP_ORDER[index + 1]
    return None


def fmt_num(value: float) -> str:
    """170.0 -> 170；170.5 原样。档案展示与记忆块共用。"""
    return f"{value:.0f}" if float(value).is_integer() else f"{value}"


def clean_value(raw: object) -> str:
    """入库前的确定性清洗：去控制字符与换行、压缩空白、截断到上限。

    字段值会被拼进 **system prompt** 里的记忆块，那是比用户轮次更高权限的
    通道，所以清洗发生在写入侧（这里）而不只是读取侧。
    """
    text = _CONTROL.sub(" ", str(raw))
    return " ".join(text.split())[:MAX_FIELD_CHARS]


def parse_date(text: str, *, allow_year_only: bool = False) -> date | None:
    """解析日期文本：ISO（YYYY-MM-DD）、中文年月日、或降低精度（见下）。

    **降低精度**（只给年月的按当月 1 号、只给年的按 1 月 1 号）在中文形式里
    本来就被接受（`2020年5月`），所以 ISO 的 `2020-05` 也必须接受 ——
    指令 §五 与确诊时间的错误文案都在教用户写 `2020-05`，拒收它等于自己
    打自己脸。`allow_year_only` 只额外放开「纯年份」那一种（`2020`）。
    """
    if not text:
        return None
    text = text.strip()
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"^(\d{4})-(\d{1,2})$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), 1)
        except ValueError:
            return None
    m = re.match(r"^(\d{4})年\s*(\d{1,2})月(?:\s*(\d{1,2})日)?$", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
        except ValueError:
            return None
    if allow_year_only:
        m = re.match(r"^(\d{4})$", text)
        if m:
            try:
                return date(int(m.group(1)), 1, 1)
            except ValueError:
                return None
    return None


def _to_float(text: str) -> float | None:
    """取文本里的第一个数字（容忍「170」「170.5」「170cm」「170 厘米」）。"""
    m = _NUMBER.search(text)
    if m is None:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _missing(step: str) -> tuple[None, str]:
    """未提供值：可跳过项放行，必填项拒绝。"""
    return None, MSG.onboarding_required_field(STEP_LABELS[step])


def validate_step(step: str, raw: object | None = None) -> tuple[object | None, str | None]:
    """校验建档单步输入 → ``(归一化值, 错误文案)``，两者互斥。

    ``raw`` 为 None / 空串表示「用户没有提供」（跳过或没听懂）：
    可跳过项返回 ``(None, None)``，必填项返回错误文案。

    归一化值：gender → str，birth_date / diagnosed_date → date，
    height_cm / weight_kg → float，disease_name / nickname → str。
    """
    if step not in STEP_ORDER:
        return None, MSG.onboarding_unknown_field(clean_value(step), STEP_ORDER)

    text = clean_value(raw) if raw is not None else ""
    if not text:
        if step in SKIPPABLE:
            return None, None
        return _missing(step)

    if step == "nickname":
        # 自由文本：`clean_value` 已去掉控制字符并折叠空白（该值最终会被注入
        # system prompt，用户可控文本不许撑破记忆块结构）。只额外管长度。
        if len(text) > NICKNAME_MAX_CHARS:
            return (
                None,
                MSG.onboarding_long_nickname(NICKNAME_MAX_CHARS, text),
            )
        return text, None

    if step == "gender":
        if text not in GENDERS:
            return None, MSG.onboarding_invalid_gender(text)
        return text, None

    if step == "birth_date":
        parsed = parse_date(text, allow_year_only=True)
        if parsed is None or parsed < BIRTH_MIN or parsed > date.today():
            return (
                None,
                MSG.onboarding_invalid_birth_date(text, BIRTH_MIN.isoformat()),
            )
        return parsed, None

    if step == "height_cm":
        value = _to_float(text)
        if value is None or not (HEIGHT_MIN <= value <= HEIGHT_MAX):
            return None, MSG.onboarding_invalid_height(HEIGHT_MIN, HEIGHT_MAX, text)
        return value, None

    if step == "weight_kg":
        value = _to_float(text)
        if value is None or not (WEIGHT_MIN <= value <= WEIGHT_MAX):
            return None, MSG.onboarding_invalid_weight(WEIGHT_MIN, WEIGHT_MAX, text)
        return value, None

    if step == "disease_name":
        if text != SUPPORTED_DISEASE:
            return None, MSG.onboarding_unsupported_disease
        return text, None

    # diagnosed_date
    parsed = parse_date(text, allow_year_only=True)
    if parsed is None:
        return (
            None,
            MSG.onboarding_invalid_diagnosed_date(text),
        )
    if parsed > date.today():
        return None, MSG.onboarding_future_diagnosed_date(parsed.isoformat())
    return parsed, None


def age_on(birth: date, today: date | None = None) -> int:
    """周岁 —— 记忆块展示用（确定性，不引入 LLM 估算）。"""
    moment = today or datetime.now(UTC).date()
    years = moment.year - birth.year
    if (moment.month, moment.day) < (birth.month, birth.day):
        years -= 1
    return years
