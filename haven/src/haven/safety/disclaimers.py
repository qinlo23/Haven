"""免责声明模板 —— 固定文案，绝不 LLM 生成。

值从 `src/haven/safety/disclaimers.py` 原样复刻。
"""

from enum import Enum


class DisclaimerType(Enum):
    FIRST_USE = "first_use"
    KNOWLEDGE_QA = "knowledge_qa"
    RISK_ASSESSMENT = "risk_assessment"
    MEDICAL_ADVICE = "medical_advice"


DISCLAIMERS: dict[DisclaimerType, str] = {
    DisclaimerType.FIRST_USE: (
        "健健（Haven）是你的私人健康顾问，不是医疗设备，不提供医疗诊断、"
        "处方或治疗建议。所有健康建议仅供参考，请以医生的专业意见为准。"
        "如遇紧急情况，请立即拨打 120 或前往最近医院急诊科。"
    ),
    DisclaimerType.KNOWLEDGE_QA: (
        "以上信息仅供参考，不能替代专业医疗建议。如有疑问，请咨询医生。"
    ),
    DisclaimerType.RISK_ASSESSMENT: (
        "本风险评估基于公开的临床指南，仅供参考，不构成医疗诊断。"
        "如有任何不适，请及时就医。"
    ),
    DisclaimerType.MEDICAL_ADVICE: (
        "健健不提供医疗诊断或治疗建议。以上内容仅供参考，"
        "请咨询医生获取专业的医疗建议。如遇紧急情况，请立即拨打 120。"
    ),
}


def get_disclaimer(disclaimer_type: DisclaimerType) -> str:
    return DISCLAIMERS[disclaimer_type]
