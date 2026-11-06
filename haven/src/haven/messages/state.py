"""对话状态管理 —— 消息列表的查询、修剪与摘要。

提供纯函数，不持有任何 LangGraph State —— 只做确定性计算。
当前中间件 ``_text.py`` 的 ``human_text()`` 功能在此以面向消息列表的口径重新提供。

过渡期
    中间件如需修剪消息、查询最后一条人类/AI 消息，引用 ``conversation``。

远期
    直接对接 LangGraph ``StateGraph`` 的 ``messages`` 键的 reducer 逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


@dataclass
class ConversationState:
    """一次对话的消息状态视图。

    全部方法为纯函数，可以从任何中间件或工具安全调用。
    """

    #: 保留的最近消息数上限（超出则头部修剪）。
    max_messages: int = 50
    #: 列表开头保留的系统消息数（不受修剪影响）。
    system_message_count: int = 1

    def trim(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """修剪消息列表到 ``max_messages``。

        保留开头的系统消息 + 尾部最近的对话消息。
        """
        if len(messages) <= self.max_messages:
            return messages
        system = messages[: self.system_message_count]
        recent = messages[-(self.max_messages - self.system_message_count) :]
        return system + recent

    @staticmethod
    def last_human_text(messages: list[BaseMessage]) -> str:
        """最近一条 ``HumanMessage`` 的纯文本。

        与 ``middleware/_text.py:human_text()`` 同一口径 —— 处理
        ``str`` 和 ``list[dict]`` 两种 content 形态。
        """
        from langchain_core.messages import HumanMessage

        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = message.content
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    parts = [
                        block.get("text", "")
                        for block in content
                        if isinstance(block, dict) and block.get("type") == "text"
                    ]
                    return "".join(parts)
        return ""

    @staticmethod
    def last_ai_text(messages: list[BaseMessage]) -> str:
        """最近一条 ``AIMessage`` 的纯文本（不含 tool_calls）。"""
        from langchain_core.messages import AIMessage

        for message in reversed(messages):
            if isinstance(message, AIMessage) and isinstance(message.content, str):
                return message.content
        return ""


#: 模块级实例，供中间件引用。
conversation = ConversationState()
