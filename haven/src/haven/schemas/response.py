"""Agent 回复结构化输出 —— 远期 LangGraph 迁移时用于 State 节点。

用途：约束模型的最终回复为固定结构（message_type + content + data），
      方便前端/API 按类型渲染不同 UI（如血压确认卡片 vs 普通文本）。
      当前 MDA 阶段不直接使用，LangGraph 迁移时作为 respond_node 的输出 schema。

消费者：远期 `haven/graph.py` 的 respond 节点：
        `model.with_structured_output(AgentResponse)`
"""

from typing import Literal

from pydantic import BaseModel, Field


class AgentResponse(BaseModel):
    """模型对用户的最终回复 —— 带类型标记的结构化消息。

    当前 MDA 阶段为预留定义（不参与运行时）；LangGraph 迁移后，
    respond 节点用 `model.with_structured_output(AgentResponse)` 约束输出。
    """

    message_type: Literal[
        "greeting",
        "bp_recorded",
        "bp_confirm_needed",
        "bp_confirmed",
        "bp_cancelled",
        "trend",
        "onboarding",
        "onboarding_done",
        "consent",
        "help",
        "error",
        "chat",
    ] = Field(description="消息类型，供前端按类型渲染不同 UI 组件")
    content: str = Field(description="展示给用户的人类可读文本")
    data: dict | None = Field(
        default=None,
        description="附加结构化数据（如血压值、趋势统计），供 UI 组件绑定",
    )
