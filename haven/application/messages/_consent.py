"""隐私同意相关固定回复文案。"""

from __future__ import annotations

from application.commands import C_CONSENT, C_PROFILE

consent_required = (
    f"你还没有同意隐私政策。为保护你的健康数据，使用记录/建档前请先同意：请输入 {C_CONSENT}。"
)
consent_already = "你已同意隐私政策。"
consent_granted = f"已同意隐私政策。输入 {C_PROFILE} 建档，或直接告诉我血压值（如 120/80）。"

consent_guidance = f"\n\n如你同意以上内容，请输入 {C_CONSENT} 以继续。"
