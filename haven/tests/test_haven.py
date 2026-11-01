"""独立入口基础回归：模拟模型与数据库边界，不调用外部服务。"""

from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from langchain.tools import ToolRuntime
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langsmith import tracing_context
from pydantic import Field

from application.messages import MSG
from haven import HavenSession, create_haven, run_once
from messages.instructions import system_prompt
from middleware.memory_context import MemoryInjectionMiddleware
from safety.degradation import LLM_DEGRADED_RESPONSE
from safety.emergency import EMERGENCY_RESPONSE_CN
from safety.output_filter import SAFE_FALLBACK
from tools.consent import consent_status
from tools.permissions import interrupt_on


class ScriptedModel(BaseChatModel):
    """异步返回预设消息，并捕获实际模型输入和工具定义。"""

    replies: list[AIMessage] = Field(default_factory=list)
    seen: list[list] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "haven-offline-test"

    def bind_tools(self, tools, **kwargs):
        self.tool_names = [tool.name for tool in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("测试只能异步调用")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if not self.replies:
            raise RuntimeError("模拟模型故障")
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


class HavenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(tracing_context(enabled=False))
        # 健康摘要与急救审计均在数据库边界替换，测试不接触 .env 指向的库。
        self.memory = self.enterContext(
            patch("middleware.memory_context._memory_block", AsyncMock(return_value="测试摘要"))
        )
        self.enterContext(
            patch("middleware.emergency_input.EmergencyInputMiddleware._record_audit", AsyncMock())
        )

    async def test_greeting_and_emergency_skip_model(self):
        model = ScriptedModel()
        self.assertEqual(await run_once("/hello", model=model), MSG.greeting)
        self.assertEqual(await run_once("/help 我胸痛", model=model), EMERGENCY_RESPONSE_CN)
        self.assertEqual(model.seen, [])
        self.memory.assert_not_awaited()

    async def test_prompt_once_and_summary_not_in_history(self):
        model = ScriptedModel(replies=[AIMessage(content="收到")])
        agent = create_haven(model=model)
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="请继续聊聊")]},
            config={"configurable": {"thread_id": "prompt-test"}},
        )
        prompt = model.seen[0][0]
        self.assertIsInstance(prompt, SystemMessage)
        instructions = (Path(__file__).resolve().parents[1] / "instructions.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(prompt.text.count(instructions), 1)
        self.assertEqual(prompt.text.count("测试摘要"), 1)
        self.assertNotIn("managed_memory_policy", prompt.text)
        self.assertFalse(any(isinstance(m, SystemMessage) for m in result["messages"]))
        self.assertEqual(len(model.tool_names), 11)
        self.assertTrue(set(interrupt_on()).issubset(model.tool_names))

    async def test_normal_tool_gets_native_runtime(self):
        received = []

        async def fake_status(runtime=None):
            received.append(runtime)
            return "测试同意状态"

        model = ScriptedModel(
            replies=[
                AIMessage(
                    content="", tool_calls=[{"name": "consent_status", "args": {}, "id": "status"}]
                ),
                AIMessage(content="状态已查询"),
            ]
        )
        with patch.object(consent_status, "coroutine", fake_status):
            self.assertEqual(await run_once("查看隐私同意状态", model=model), "状态已查询")
        self.assertEqual(len(received), 1)
        self.assertIsInstance(received[0], ToolRuntime)
        self.assertTrue(any(isinstance(m, ToolMessage) for m in model.seen[-1]))

    async def test_sensitive_tools_pause_before_handler(self):
        for name in interrupt_on():
            with self.subTest(tool=name):
                model = ScriptedModel(
                    replies=[
                        AIMessage(
                            content="", tool_calls=[{"name": name, "args": {}, "id": "sensitive"}]
                        ),
                    ]
                )
                with patch(
                    "middleware.observability.ObservabilityMiddleware.awrap_tool_call",
                    AsyncMock(side_effect=AssertionError("敏感工具不得进入执行链")),
                ) as handler:
                    reply = await HavenSession(model=model).send("执行刚才请求的操作")
                    handler.assert_not_awaited()
                self.assertTrue(reply.pending)
                self.assertIn(name, reply.text)
                self.assertIn("/approve", reply.text)

    async def test_protected_tool_still_requires_consent(self):
        @asynccontextmanager
        async def fake_session():
            yield object()

        model = ScriptedModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "get_health_profile", "args": {}, "id": "profile"}],
                ),
                AIMessage(content="请先同意隐私政策"),
            ]
        )
        with (
            patch("middleware.consent_policy.session_scope", fake_session),
            patch("middleware.consent_policy.has_consented", AsyncMock(return_value=False)),
            patch(
                "middleware.mcp_policy.McpPolicyMiddleware.awrap_tool_call", AsyncMock()
            ) as handler,
        ):
            await run_once("查看健康档案", model=model)
            handler.assert_not_awaited()
        replies = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
        self.assertEqual(replies[-1].content, MSG.consent_required)

    async def test_model_failure_degrades(self):
        self.assertEqual(await run_once("继续聊聊", model=ScriptedModel()), LLM_DEGRADED_RESPONSE)

    async def test_memory_failure_preserves_instructions(self):
        self.memory.side_effect = RuntimeError("模拟数据库不可用")
        model = ScriptedModel(replies=[AIMessage(content="收到")])
        self.assertEqual(await run_once("继续聊聊", model=model), "收到")
        self.assertNotIn("测试摘要", model.seen[0][0].text)
        self.assertNotIn("managed_memory_policy", model.seen[0][0].text)

    async def test_unsafe_output_is_filtered(self):
        model = ScriptedModel(replies=[AIMessage(content="你确诊了高血压")])
        self.assertEqual(await run_once("继续聊聊", model=model), SAFE_FALLBACK)

    def test_mda_default_memory_behavior_is_preserved(self):
        self.assertTrue(MemoryInjectionMiddleware().inject_base_prompt)
        self.assertIn("managed_memory_policy", system_prompt.build())


if __name__ == "__main__":
    unittest.main()
