"""消息构造器 —— 用 LangChain 原生消息类型显式构建每一条消息。

替代 MDA Ingress 中隐式的 ``{"messages": [{"role": "human", "content": text}]}``
构造，以及 MDA 中间件链中对 system prompt 的隐性操作。

当前你的中间件已经在显式使用 ``AIMessage``、``SystemMessage``、``ToolMessage``
—— 本模块是它们的集中管理入口，确保所有消息构造走同一个口径。

过渡期
    中间件和工具引用 ``builder.ai(...)`` / ``builder.tool(...)`` 等工厂方法，
    替代裸 ``AIMessage(content=...)``。

远期
    ``builder.from_ingress()`` 产出 LangGraph State 的初始 ``messages`` 列表。
"""

from __future__ import annotations

from typing import Any, cast

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.content import ContentBlock


class MessageBuilder:
    """LangChain 原生消息类型的集中构造器。"""

    @staticmethod
    def human(text: str) -> HumanMessage:
        """用户消息。"""
        return HumanMessage(content=text)

    @staticmethod
    def ai(text: str) -> AIMessage:
        """模型回复消息。"""
        return AIMessage(content=text)

    @staticmethod
    def system(text: str) -> SystemMessage:
        """系统提示（纯文本 content）。"""
        return SystemMessage(content=text)

    @staticmethod
    def system_blocks(blocks: list[dict[str, Any]]) -> SystemMessage:
        """系统提示（content_blocks 形式，保留 cache_control 等标记）。

        这是 MDA 中间件链的标准注入方式 —— 用 ``content_blocks`` 追加块，
        不压平已有中间件设下的缓存标记。
        """
        return SystemMessage(content_blocks=cast("list[ContentBlock]", blocks))

    @staticmethod
    def tool(content: str, *, tool_call_id: str, name: str = "") -> ToolMessage:
        """工具返回消息。"""
        return ToolMessage(content=content, tool_call_id=tool_call_id, name=name)

    @staticmethod
    def from_ingress(
        user_text: str, system_prompt_text: str | None = None
    ) -> list[BaseMessage]:
        """构造一次对话的初始消息列表（替代 MDA Ingress）。

        远期 LangGraph 迁移后，这就是放进 ``State["messages"]`` 的初始值。
        """
        messages: list[BaseMessage] = []
        if system_prompt_text:
            messages.append(SystemMessage(content=system_prompt_text))
        messages.append(HumanMessage(content=user_text))
        return messages


#: 模块级实例，供中间件和工具直接引用。
builder = MessageBuilder()
