# 对话消息构造与状态管理；面向用户的产品文案统一见 application.messages。
"""消息层 —— 用 LangChain 原生消息类型显式控制 agent 的输入/输出/System Prompt。

本模块的目标：接管 MDA 隐性管理的消息部分，用显式的
``SystemMessage`` / ``HumanMessage`` / ``AIMessage`` / ``ToolMessage`` 构建
每一条输入与输出。

过渡期（MDA 模式）
    MDA 继续编译和运行 agent（``mda dev`` / ``mda deploy`` 不变），
    但 system prompt 组装、消息构造、状态管理等由本模块显式控制。
    中间件引用本模块的 ``MessageBuilder``、``SystemPrompt`` 统一消息出入口。

远期（LangGraph 迁移）
    本模块直接对接 ``StateGraph`` 的 ``messages`` 状态键，
    不再经过 MDA 的 ``define_deep_agent`` → ``compile_managed_agent``。
"""

from __future__ import annotations

from haven.messages.builder import MessageBuilder, builder
from haven.messages.entry import AgentInput
from haven.messages.instructions import SystemPrompt, system_prompt
from haven.messages.state import ConversationState, conversation

__all__ = [
    "AgentInput",
    "builder",
    "ConversationState",
    "conversation",
    "MessageBuilder",
    "system_prompt",
    "SystemPrompt",
]
