"""Agent 输入构造 —— 替代 MDA Ingress 的消息标准化。

MDA 的 ``_ingress.py`` 将外部事件标准化为
``{"messages": [{"role": "human", "content": text}]}`` 再调用 agent。
本模块显式定义这个标准化过程，让你控制消息的格式与内容。

过渡期
    这里的构造逻辑与 MDA 并行 —— MDA 的 Ingress 仍负责 HTTP 入口和签名
    验证，但消息格式由本模块定义，保持一致性。

远期
    你自己的 HTTP 服务直接调用 ``AgentInput.as_messages()`` 构造
    LangGraph State 的初始输入，不经过 MDA Ingress。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from haven.messages.instructions import system_prompt


@dataclass(frozen=True)
class AgentInput:
    """一次 Agent 调用的标准化输入。

    显式替代 MDA 的 ``{"messages": [{"role": "human", "content": text}]}``。
    """

    #: 用户输入的纯文本。
    user_text: str
    #: LangGraph 线程 ID（空=新对话）。
    thread_id: str | None = None

    def as_langgraph_input(self) -> dict[str, Any]:
        """转为 LangGraph Server API 的 ``input`` 格式。

        这是 MDA 的 ``POST /threads/{tid}/runs`` 认识的格式。
        过渡期：你自己的入口层可用此方法构造请求体。
        """
        return {
            "messages": [
                {
                    "role": "human",
                    "content": self.user_text,
                }
            ]
        }

    def as_messages(self) -> list[dict[str, str]]:
        """转为 LangChain 消息列表（远期直接放进 State 的 messages 键）。

        包含显式构造的 system prompt + 用户消息。
        """
        return [
            {"role": "system", "content": system_prompt.build()},
            {"role": "human", "content": self.user_text},
        ]
