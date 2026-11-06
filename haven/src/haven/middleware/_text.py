"""中间件共享：取最近一条用户消息的纯文本。"""

from __future__ import annotations

from langchain_core.messages import HumanMessage


def human_text(request) -> str:
    """最近一条用户消息的纯文本（可能为空，处理 str/list 两种 content）。"""
    for message in reversed(request.messages):
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
