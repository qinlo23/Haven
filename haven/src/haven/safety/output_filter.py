"""输出安全过滤 —— 确定性正则，命中即整条替换为 SAFE_FALLBACK。

值从 `src/haven/safety/output_filter.py` 原样复刻。
"""

import re

DIAGNOSTIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"你患[有了]"),
    re.compile(r"你得了"),
    re.compile(r"你可能是"),
    re.compile(r"诊断[为你]"),
    re.compile(r"确诊[为你]"),
    re.compile(r"你属于.*患者"),
    re.compile(r"你的病情"),
    re.compile(r"你[确肯]定是"),
]

PRESCRIPTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"你应该吃"),
    re.compile(r"建议你服用"),
    re.compile(r"你需?要?服用"),
    re.compile(r"推荐你?使?用"),
    re.compile(r"剂量.*调整"),
    re.compile(r"加药|减药|停药|换药"),
]

BLOCKED_SENTENCES: frozenset[str] = frozenset(
    {
        "根据你的情况，你患有",
        "我诊断你为",
        "你确诊了",
        "你需要服用",
        "我建议你吃",
    }
)

SAFE_FALLBACK: str = (
    "健健无法提供医疗诊断或用药建议。"
    "如有健康疑问，请咨询医生。如遇紧急情况，请立即拨打 120。"
)


def filter_output(text: str) -> str:
    for blocked in BLOCKED_SENTENCES:
        if blocked in text:
            return SAFE_FALLBACK

    for pattern in DIAGNOSTIC_PATTERNS:
        if pattern.search(text):
            return SAFE_FALLBACK

    for pattern in PRESCRIPTION_PATTERNS:
        if pattern.search(text):
            return SAFE_FALLBACK

    return text


def is_safe(text: str) -> bool:
    return filter_output(text) == text
