"""真实顶层图回归：复用业务场景，并验证项目级路由与审批恢复。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import test_hypertension
from langchain_core.messages import AIMessage
from test_react import MockModel

from application.messages import MSG
from haven import HavenSession, create_haven
from middleware.commands import command_middleware
from safety.degradation import LLM_DEGRADED_RESPONSE
from safety.emergency import EMERGENCY_RESPONSE_CN
from storage.models import (
    AuditLog,
    BloodPressure,
    ConsentRecord,
    OnboardingDraft,
    PendingBpConfirmation,
)


class HavenGraphTests(test_hypertension.HypertensionTests):
    """同一组业务用例改为真实 Haven 父图，数据库仍为每例独立临时库。"""

    def graph(self, model):
        return create_haven(model=model)

    async def test_hello_and_help_skip_business_model_and_memory(self):
        model = MockModel()
        graph = self.graph(model)
        with patch("middleware.memory_context._memory_block", AsyncMock()) as memory:
            for text, expected in (("/hello", MSG.greeting), ("/help", MSG.help_text())):
                result = await self.send(graph, text)
                self.assertEqual(result["messages"][-1].content, expected)
                self.assertTrue(result["handled"])
                self.assertEqual(result["model_calls"], 0)
            memory.assert_not_awaited()
        self.assertEqual(model.seen, [])

    async def test_consent_command_uses_real_database_without_model(self):
        model = MockModel()
        graph = self.graph(model)
        for _ in range(2):
            result = await self.send(graph, "/consent")
            self.assertTrue(result["handled"])
        self.assertEqual(len(await self.rows(ConsentRecord)), 1)
        self.assertTrue(any(row.event_type == "consent" for row in await self.rows(AuditLog)))
        self.assertEqual(model.seen, [])

    async def test_cancel_command_clears_pending_and_draft_without_model(self):
        await self.seed_all_business_tables()
        model = MockModel()
        result = await self.send(self.graph(model), "/cancel")
        self.assertEqual(result["messages"][-1].content, MSG.bp_cancelled)
        self.assertEqual(await self.rows(PendingBpConfirmation), [])
        self.assertEqual(await self.rows(OnboardingDraft), [])
        self.assertEqual(len(await self.rows(BloodPressure)), 1)
        self.assertEqual(model.seen, [])

    async def test_emergency_precedes_cancel_and_preserves_business_data(self):
        await self.seed_all_business_tables()
        model = MockModel()
        result = await self.send(self.graph(model), "/cancel 我胸痛")
        self.assertEqual(result["messages"][-1].content, EMERGENCY_RESPONSE_CN)
        self.assertEqual(len(await self.rows(PendingBpConfirmation)), 1)
        self.assertEqual(len(await self.rows(OnboardingDraft)), 1)
        self.assertTrue(any(row.event_type == "emergency" for row in await self.rows(AuditLog)))
        self.assertEqual(model.seen, [])

    async def test_approval_resumes_child_without_replaying_entry(self):
        await self.seed_all_business_tables()
        model = MockModel(
            replies=[
                test_hypertension.call("delete_my_data", "delete"),
                AIMessage(content="操作已处理"),
            ]
        )
        graph = self.graph(model)
        with patch.object(
            command_middleware,
            "awrap_model_call",
            AsyncMock(wraps=command_middleware.awrap_model_call),
        ) as route:
            pending = await self.send(graph, "确认删除模拟数据")
            self.assertEqual(route.await_count, 1)
            self.assertEqual(len(model.seen), 1)
            result = await self.resume(graph, pending)
            self.assertEqual(route.await_count, 1)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(await self.rows(BloodPressure), [])

    async def test_session_budget_is_reset_for_next_request(self):
        model = MockModel(
            replies=[
                *[
                    test_hypertension.call("get_consent_policy", f"policy-{index}")
                    for index in range(8)
                ],
                AIMessage(content="新的请求回复"),
            ]
        )
        session = HavenSession(model=model)
        reply = await session.send("模拟循环请求")
        self.assertEqual(reply.text, LLM_DEGRADED_RESPONSE)
        self.assertFalse(reply.failed)
        self.assertEqual(len(model.seen), 8)
        self.assertEqual((await session.send("/hello")).text, MSG.greeting)
        self.assertEqual((await session.send("继续聊聊")).text, "新的请求回复")
        self.assertEqual(len(model.seen), 9)
