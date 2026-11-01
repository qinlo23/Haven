"""业务子图集成回归：模拟模型、临时 SQLite，不读取真实健康数据库。"""

from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from langsmith import tracing_context
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_react import MockModel

from agents.hypertension import build_hypertension_agent
from application.messages import MSG
from reasoning import ReactState, build_react_graph
from safety.degradation import DB_DEGRADED_RESPONSE, LLM_DEGRADED_RESPONSE
from safety.output_filter import SAFE_FALLBACK
from storage import database
from storage.models import (
    AuditLog,
    BloodPressure,
    ConsentRecord,
    DiseaseRecord,
    HealthProfile,
    OnboardingDraft,
    PendingBpConfirmation,
)
from tools.consent import record_consent
from tools.permissions import TOOL_PERMISSIONS, interrupt_on


def call(name: str, call_id: str, **args) -> AIMessage:
    """生成一个模拟模型工具调用，不进行模型推理。"""
    return AIMessage(content="", tool_calls=[{"name": name, "id": call_id, "args": args}])


class HypertensionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(tracing_context(enabled=False))
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.owner = "hypertension-test-owner"
        url = f"sqlite+aiosqlite:///{Path(self.directory) / 'test.db'}"
        engine = create_async_engine(url, echo=False)
        self.addAsyncCleanup(engine.dispose)
        async with engine.begin() as connection:
            await connection.run_sync(database.Base.metadata.create_all)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)
        self.enterContext(patch.object(database, "_engine", engine))
        self.enterContext(patch.object(database, "_session_factory", self.sessions))
        self.enterContext(patch.object(database, "_init_done", True))
        settings = SimpleNamespace(owner_id=self.owner, database_url=url, mcp_servers={})
        self.enterContext(patch.object(database, "get_settings", return_value=settings))
        self.enterContext(patch("middleware.mcp_policy.get_settings", return_value=settings))
        self.config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 100}

    def graph(self, model):
        child = build_hypertension_agent(model=model)

        def entry(state):
            # 测试父图只负责请求初始化；生产急救和命令路由留到第四步接入。
            return {"model_calls": 0, "pending_tools": [], "tool_index": 0, "review_decisions": {}}

        graph = StateGraph(ReactState)
        graph.add_node("entry", entry)
        graph.add_node("hypertension", child)
        graph.add_edge(START, "entry")
        graph.add_edge("entry", "hypertension")
        graph.add_edge("hypertension", END)
        return graph.compile(checkpointer=InMemorySaver())

    async def send(self, graph, text="模拟健康管理请求"):
        return await graph.ainvoke({"messages": [HumanMessage(content=text)]}, self.config)

    async def resume(self, graph, pending, decision="approve"):
        return await graph.ainvoke(
            Command(
                resume={
                    item.id: {
                        "decisions": [{"type": decision} for _ in item.value["action_requests"]]
                    }
                    for item in pending["__interrupt__"]
                }
            ),
            self.config,
        )

    async def rows(self, model):
        async with self.sessions() as session:
            return list((await session.execute(select(model))).scalars().all())

    async def consent(self):
        # 准备数据也使用真实工具，数据库和本人身份均已显式切换到本测试环境。
        await record_consent.ainvoke({})
        self.assertEqual(len(await self.rows(ConsentRecord)), 1)

    async def pending_pressure(self, model_replies):
        await self.consent()
        model = MockModel(
            replies=[
                call("record_blood_pressure", "record", systolic=170, diastolic=105),
                AIMessage(content="请确认测量数据"),
                *model_replies,
            ]
        )
        graph = self.graph(model)
        result = await self.send(graph, "记录模拟异常血压")
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(len(await self.rows(PendingBpConfirmation)), 1)
        self.assertEqual(await self.rows(BloodPressure), [])
        return graph, model

    def test_registration_and_middleware_boundaries(self):
        with patch("agents.hypertension.build_react_graph", wraps=build_react_graph) as build:
            child = build_hypertension_agent(model=MockModel())
        options = build.call_args.kwargs
        self.assertEqual({tool.name for tool in options["tools"]}, set(TOOL_PERMISSIONS))
        self.assertEqual(len(options["tools"]), 11)
        self.assertEqual(options["approval_rules"], interrupt_on())
        self.assertEqual(
            [m.name for m in options["model_middleware"]],
            ["haven_output_safety", "haven_memory_context"],
        )
        self.assertEqual(
            [m.name for m in options["tool_middleware"]],
            ["haven_observability", "haven_consent_policy", "haven_mcp_policy"],
        )
        self.assertIsNone(child.checkpointer)
        self.assertFalse(options["model_middleware"][1].inject_base_prompt)

    async def test_policy_and_status_do_not_require_consent(self):
        model = MockModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "get_consent_policy", "id": "policy", "args": {}},
                        {"name": "consent_status", "id": "status", "args": {}},
                    ],
                ),
                AIMessage(content="请先同意隐私政策"),
            ]
        )
        result = await self.send(self.graph(model))
        tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual([message.tool_call_id for message in tools], ["policy", "status"])
        self.assertTrue(all(message.status == "success" for message in tools))
        self.assertEqual(await self.rows(ConsentRecord), [])

    async def test_no_consent_blocks_write_and_hides_existing_profile(self):
        async with self.sessions.begin() as session:
            session.add(
                HealthProfile(
                    user_id=self.owner,
                    nickname="隔离测试称呼",
                    gender="男",
                    birth_date=date(1960, 1, 1),
                )
            )
        model = MockModel(
            replies=[
                call("record_blood_pressure", "record", systolic=128, diastolic=84),
                AIMessage(content="请先同意隐私政策"),
            ]
        )
        result = await self.send(self.graph(model))
        self.assertEqual(await self.rows(BloodPressure), [])
        output = next(message for message in result["messages"] if isinstance(message, ToolMessage))
        self.assertEqual(output.content, MSG.consent_required)
        self.assertNotIn("隔离测试称呼", model.seen[0][0].text)

    async def test_consent_profile_and_normal_pressure_use_real_tools(self):
        model = MockModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "record_consent", "id": "consent", "args": {}},
                        {
                            "name": "save_health_profile",
                            "id": "profile",
                            "args": {
                                "nickname": "模拟称呼",
                                "gender": "男",
                                "birth_date": "1960-01-01",
                            },
                        },
                        {
                            "name": "record_blood_pressure",
                            "id": "record",
                            "args": {
                                "systolic": 128,
                                "diastolic": 84,
                            },
                        },
                    ],
                ),
                AIMessage(content="操作已处理"),
            ]
        )
        await self.send(self.graph(model))
        self.assertEqual(len(await self.rows(ConsentRecord)), 1)
        profile = (await self.rows(HealthProfile))[0]
        pressure = (await self.rows(BloodPressure))[0]
        self.assertEqual(profile.nickname, "模拟称呼")
        self.assertEqual((pressure.systolic, pressure.diastolic), (128, 84))
        self.assertEqual(pressure.user_id, self.owner)
        self.assertEqual(await self.rows(PendingBpConfirmation), [])
        self.assertIn("模拟称呼", model.seen[-1][0].text)
        self.assertIn("最近一次血压：128/84", model.seen[-1][0].text)
        audits = await self.rows(AuditLog)
        self.assertTrue(any(item.event_type == "consent" for item in audits))
        self.assertTrue(
            any(item.event_summary == "tool:record_blood_pressure:ok" for item in audits)
        )
        self.assertTrue(
            all(
                "128/84" not in item.event_summary and "模拟称呼" not in item.event_summary
                for item in audits
            )
        )

    async def test_abnormal_record_only_saves_after_matching_approval(self):
        graph, model = await self.pending_pressure(
            [
                call("confirm_abnormal_blood_pressure", "confirm", systolic=170, diastolic=105),
                AIMessage(content="操作已处理"),
            ]
        )
        pending = await self.send(graph, "确认模拟记录")
        self.assertTrue(pending.get("__interrupt__"))
        self.assertEqual(await self.rows(BloodPressure), [])
        result = await self.resume(graph, pending)
        pressure = (await self.rows(BloodPressure))[0]
        self.assertEqual((pressure.systolic, pressure.diastolic), (170, 105))
        self.assertEqual(await self.rows(PendingBpConfirmation), [])
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(len(model.seen), 4)

    async def test_draft_profile_pending_and_trend_tools_execute(self):
        await self.consent()
        model = MockModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "save_onboarding_draft",
                            "id": "draft",
                            "args": {
                                "step": "nickname",
                                "value": "模拟未完成建档",
                            },
                        },
                        {"name": "get_health_profile", "id": "profile", "args": {}},
                        {"name": "get_pending_blood_pressure", "id": "pending", "args": {}},
                        {"name": "get_seven_day_trend", "id": "trend", "args": {}},
                    ],
                ),
                AIMessage(content="继续建档"),
            ]
        )
        result = await self.send(self.graph(model))
        draft = (await self.rows(OnboardingDraft))[0]
        self.assertEqual(draft.nickname, "模拟未完成建档")
        self.assertEqual(draft.next_field, "gender")
        outputs = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual(
            [message.tool_call_id for message in outputs], ["draft", "profile", "pending", "trend"]
        )
        self.assertTrue(all(message.status == "success" for message in outputs))
        self.assertIn(f"message={MSG.bp_no_pending!r}", outputs[2].content)
        self.assertIn("record_count=0", outputs[3].content)
        self.assertIn("模拟未完成建档", model.seen[-1][0].text)

    async def test_rejected_abnormal_record_remains_pending(self):
        graph, _ = await self.pending_pressure(
            [
                call("confirm_abnormal_blood_pressure", "confirm", systolic=170, diastolic=105),
                AIMessage(content="操作已拒绝"),
            ]
        )
        result = await self.resume(graph, await self.send(graph, "确认模拟记录"), "reject")
        self.assertEqual(await self.rows(BloodPressure), [])
        self.assertEqual(len(await self.rows(PendingBpConfirmation)), 1)
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and message.status == "error"
                for message in result["messages"]
            )
        )

    async def test_approval_does_not_bypass_value_matching(self):
        graph, _ = await self.pending_pressure(
            [
                call("confirm_abnormal_blood_pressure", "confirm", systolic=171, diastolic=105),
                AIMessage(content="数值不一致"),
            ]
        )
        result = await self.resume(graph, await self.send(graph, "确认模拟记录"))
        self.assertEqual(await self.rows(BloodPressure), [])
        self.assertEqual(len(await self.rows(PendingBpConfirmation)), 1)
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and MSG.bp_confirm_miss in message.content
                for message in result["messages"]
            )
        )

    async def test_pending_record_expiring_during_approval_is_not_saved(self):
        graph, _ = await self.pending_pressure(
            [
                call("confirm_abnormal_blood_pressure", "confirm", systolic=170, diastolic=105),
                AIMessage(content="记录已过期"),
            ]
        )
        pending = await self.send(graph, "确认模拟记录")
        async with self.sessions.begin() as session:
            item = (await session.execute(select(PendingBpConfirmation))).scalars().one()
            item.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        result = await self.resume(graph, pending)
        self.assertEqual(await self.rows(BloodPressure), [])
        self.assertEqual(await self.rows(PendingBpConfirmation), [])
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and MSG.bp_confirm_miss in message.content
                for message in result["messages"]
            )
        )

    async def seed_all_business_tables(self):
        await self.consent()
        now = datetime.now(UTC)
        async with self.sessions.begin() as session:
            session.add_all(
                [
                    HealthProfile(user_id=self.owner, gender="男", birth_date=date(1960, 1, 1)),
                    DiseaseRecord(
                        user_id=self.owner, disease_name="高血压", diagnosed_date=date(2020, 1, 1)
                    ),
                    BloodPressure(user_id=self.owner, systolic=128, diastolic=84, measured_at=now),
                    PendingBpConfirmation(
                        user_id=self.owner,
                        systolic=170,
                        diastolic=105,
                        measured_at=now,
                        prompt_text="模拟待确认",
                        expires_at=now + timedelta(hours=24),
                    ),
                    OnboardingDraft(user_id=self.owner, nickname="模拟草稿"),
                ]
            )

    async def test_delete_only_runs_after_approval_and_keeps_audit(self):
        await self.seed_all_business_tables()
        model = MockModel(
            replies=[call("delete_my_data", "delete"), AIMessage(content="操作已处理")]
        )
        graph = self.graph(model)
        pending = await self.send(graph, "确认删除模拟数据")
        self.assertTrue(pending.get("__interrupt__"))
        self.assertEqual(len(await self.rows(BloodPressure)), 1)
        self.assertFalse(any(item.event_type == "deletion" for item in await self.rows(AuditLog)))
        await self.resume(graph, pending)
        for table in (
            HealthProfile,
            DiseaseRecord,
            BloodPressure,
            ConsentRecord,
            PendingBpConfirmation,
            OnboardingDraft,
        ):
            self.assertEqual(await self.rows(table), [])
        audits = await self.rows(AuditLog)
        self.assertTrue(any(item.event_type == "deletion" for item in audits))
        self.assertTrue(any(item.event_type == "consent" for item in audits))
        self.assertTrue(all(item.subject_key != self.owner for item in audits))

    async def test_rejected_deletion_preserves_business_data(self):
        await self.seed_all_business_tables()
        model = MockModel(
            replies=[call("delete_my_data", "delete"), AIMessage(content="操作已拒绝")]
        )
        graph = self.graph(model)
        await self.resume(graph, await self.send(graph), "reject")
        for table in (
            HealthProfile,
            DiseaseRecord,
            BloodPressure,
            ConsentRecord,
            PendingBpConfirmation,
            OnboardingDraft,
        ):
            self.assertEqual(len(await self.rows(table)), 1)
        self.assertFalse(any(item.event_type == "deletion" for item in await self.rows(AuditLog)))

    async def test_prompt_once_and_summary_does_not_enter_history(self):
        await self.consent()
        model = MockModel(replies=[AIMessage(content="收到")])
        result = await self.send(self.graph(model))
        instructions = (Path(__file__).resolve().parents[1] / "instructions.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(model.seen[0][0].text.count(instructions), 1)
        self.assertNotIn("managed_memory_policy", model.seen[0][0].text)
        self.assertFalse(any(isinstance(message, SystemMessage) for message in result["messages"]))
        self.assertEqual(len(model.schemas), 11)
        self.assertTrue(all("runtime" not in schema["properties"] for schema in model.schemas))

    async def test_memory_failure_keeps_base_prompt_and_reply(self):
        model = MockModel(replies=[AIMessage(content="继续对话")])
        with patch(
            "middleware.memory_context._memory_block",
            AsyncMock(side_effect=RuntimeError("读取失败")),
        ):
            result = await self.send(self.graph(model))
        self.assertEqual(result["messages"][-1].content, "继续对话")
        self.assertIn("# System Prompt", model.seen[0][0].text)

    async def test_model_failure_uses_existing_fallback(self):
        result = await self.send(self.graph(MockModel()))
        self.assertEqual(result["messages"][-1].content, LLM_DEGRADED_RESPONSE)

    async def test_unsafe_output_is_replaced_before_history(self):
        model = MockModel(replies=[AIMessage(content="你确诊了高血压")])
        result = await self.send(self.graph(model))
        self.assertEqual(result["messages"][-1].content, SAFE_FALLBACK)
        self.assertFalse(any(message.content == "你确诊了高血压" for message in result["messages"]))

    async def test_database_unavailable_is_returned_by_existing_tool(self):
        await self.consent()

        @asynccontextmanager
        async def unavailable():
            raise database.DatabaseUnavailable("模拟数据库故障")
            yield  # 维持异步上下文管理器协议，不提供真实会话

        model = MockModel(
            replies=[
                call("record_blood_pressure", "record", systolic=128, diastolic=84),
                AIMessage(content="数据未保存"),
            ]
        )
        with patch("tools.vitals.session_scope", unavailable):
            result = await self.send(self.graph(model))
        self.assertEqual(await self.rows(BloodPressure), [])
        self.assertTrue(
            any(
                isinstance(message, ToolMessage) and DB_DEGRADED_RESPONSE in message.content
                for message in result["messages"]
            )
        )

    def test_missing_or_empty_instructions_stops_assembly(self):
        for content in (None, " "):
            path = Path(self.directory) / "instructions.md"
            if content is not None:
                path.write_text(content, encoding="utf-8")
            with (
                patch("agents.hypertension._INSTRUCTIONS_PATH", path),
                self.assertRaises(RuntimeError),
            ):
                build_hypertension_agent(model=MockModel())


if __name__ == "__main__":
    unittest.main()
