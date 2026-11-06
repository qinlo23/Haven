"""意图分类结构化输出 —— 远期 LangGraph 迁移时用于路由节点。

用途：对用户输入做意图分类，决定后续走哪个工具/节点。
      当前 MDA 阶段为预留定义（不参与运行时）；LangGraph 迁移后，
      classify 节点用 `model.with_structured_output(UserIntent)` 做路由。

消费者：远期 `haven/graph.py` 的 classify 节点：
        `model.with_structured_output(UserIntent)`
"""

from typing import Literal

from pydantic import BaseModel, Field


class UserIntent(BaseModel):
    """用户输入意图分类结果。

    由 LLM 分类器（`model.with_structured_output(UserIntent)`）输出，
    供 LangGraph 的条件边做路由决策。
    """

    intent: Literal[
        "record_bp",
        "check_trend",
        "onboarding",
        "consent",
        "delete_data",
        "help",
        "emergency",
        "chat",
    ] = Field(description="用户意图类别")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="分类置信度，低于阈值时可回退到通用 chat 节点",
    )
