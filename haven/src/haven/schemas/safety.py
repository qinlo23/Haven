"""安全判定结构化输出 —— 供 `middleware/output_safety.py` 使用。

用途：在确定性正则未命中时，用 LLM 对模型回复做二次安全分类，
      捕获正则无法覆盖的变体（如隐含诊断、间接用药建议）。
      `with_structured_output(SafetyVerdict)` → 约束模型只输出此 schema。

消费者：`middleware/output_safety.py` 的 `_llm_safety_check()`
        或 `OutputSafetyMiddleware._rewrite_safe()` 内的 LLM 判定分支。
"""

from typing import Literal

from pydantic import BaseModel, Field


class SafetyVerdict(BaseModel):
    """对健康助手回复文本的安全判定结果。

    由 LLM 分类器（`model.with_structured_output(SafetyVerdict)`）输出，
    供 `output_safety` 中间件在正则未命中时做二次确认。
    """

    level: Literal["safe", "diagnostic_risk", "prescription_risk"] = Field(
        description=(
            "safe: 安全，无诊断或用药建议倾向；"
            "diagnostic_risk: 文本含诊断倾向（如暗示疾病、病情判断）；"
            "prescription_risk: 文本含用药建议倾向（如推荐药物、剂量调整）"
        )
    )
    reason: str = Field(
        description="判定依据简述，不超过 30 字，供审计日志使用"
    )
