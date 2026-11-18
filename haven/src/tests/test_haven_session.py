"""进程内多轮对话和人工确认的离线回归，不操作真实健康数据库。"""

from __future__ import annotations

import asyncio
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import AsyncMock, patch

from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, ToolMessage
from langsmith import tracing_context
from test_haven import ScriptedModel

from haven import HavenSession, run_once
from haven.cli import chat
from haven.safety.emergency import EMERGENCY_CLARIFY_TEXT, EMERGENCY_RESPONSE_CN
from haven.tools.account import delete_my_data
from haven.tools.vitals import confirm_abnormal_blood_pressure


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(tracing_context(enabled=False))
        self.enterContext(
            patch("haven.middleware.memory_context._memory_block", AsyncMock(return_value="测试摘要"))
        )
        self.enterContext(patch("haven.middleware.observability._maybe_audit", AsyncMock()))
        self.enterContext(
            patch("haven.middleware.emergency_input.EmergencyInputMiddleware._record_audit", AsyncMock())
        )
        self.executed = []

        async def fake_sensitive(runtime: ToolRuntime | None = None, **kwargs):
            if runtime is None:
                raise AssertionError("工具必须注入 runtime")
            self.assertIsInstance(runtime, ToolRuntime)
            self.executed.append(runtime.tool_call_id)
            return "模拟操作完成"

        for tool in (delete_my_data, confirm_abnormal_blood_pressure):
            self.enterContext(patch.object(tool, "coroutine", fake_sensitive))

    def _session(self, *, names=("delete_my_data",)):
        model = ScriptedModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": name, "args": {}, "id": f"action-{index}"}
                        for index, name in enumerate(names)
                    ],
                ),
                AIMessage(content="操作已处理"),
            ]
        )
        return HavenSession(model=model), model

    async def test_conversation_keeps_thread_and_history(self):
        model = ScriptedModel(replies=[AIMessage(content="收到"), AIMessage(content="记得")])
        session = HavenSession(model=model)
        thread_id = session.thread_id
        await session.send("我希望每天记录一次")
        self.assertEqual((await session.send("刚才说了什么")).text, "记得")
        self.assertEqual(session.thread_id, thread_id)
        self.assertIn("我希望每天记录一次", [m.content for m in model.seen[-1]])

    async def test_each_sensitive_tool_only_runs_after_approve(self):
        for name in ("delete_my_data", "confirm_abnormal_blood_pressure"):
            with self.subTest(tool=name):
                self.executed.clear()
                session, _ = self._session(names=(name,))
                reply = await session.send("执行操作")
                self.assertTrue(reply.pending)
                self.assertIn(name, reply.text)
                self.assertIn("参数", reply.text)
                self.assertEqual(self.executed, [])
                reply = await session.send("/approve")
                self.assertFalse(reply.failed)
                self.assertFalse(reply.pending)
                self.assertEqual(self.executed, ["action-0"])

    async def test_reject_does_not_execute(self):
        session, model = self._session()
        await session.send("执行操作")
        reply = await session.send("/reject")
        self.assertFalse(reply.pending)
        self.assertFalse(reply.failed)
        self.assertEqual(self.executed, [])
        tools = [m for m in model.seen[-1] if isinstance(m, ToolMessage)]
        self.assertEqual(tools[-1].status, "error")

    async def test_other_inputs_cannot_approve_or_modify_pending(self):
        session, model = self._session()
        await session.send("执行操作")
        for text in ("好的", "同意", "/approve extra", "/cancel", "替换参数执行"):
            self.assertTrue((await session.send(text)).pending)
        self.assertEqual(self.executed, [])
        self.assertEqual(len(model.seen), 1)
        self.assertFalse((await session.send("/reject")).pending)

    async def test_emergency_during_confirmation_skips_model_and_tools(self):
        session, model = self._session()
        await session.send("执行操作")
        for text, expected in (
            ("/approve 我胸痛", EMERGENCY_RESPONSE_CN),
            ("如果发生胸痛怎么办", EMERGENCY_CLARIFY_TEXT),
        ):
            reply = await session.send(text)
            self.assertEqual(reply.text, expected)
            self.assertTrue(reply.pending)
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(self.executed, [])
        await session.send("/reject")

    async def test_batch_approval_covers_all_displayed_actions(self):
        session, _ = self._session(names=("delete_my_data", "confirm_abnormal_blood_pressure"))
        reply = await session.send("执行两项操作")
        self.assertIn("delete_my_data", reply.text)
        self.assertIn("confirm_abnormal_blood_pressure", reply.text)
        self.assertEqual(self.executed, [])
        reply = await session.send("/approve")
        self.assertFalse(reply.pending)
        self.assertFalse(reply.failed)
        self.assertCountEqual(self.executed, ["action-0", "action-1"])

    async def test_concurrent_duplicate_approval_executes_once(self):
        session, _ = self._session()
        await session.send("执行操作")
        results = await asyncio.gather(session.send("/approve"), session.send("/approve"))
        self.assertEqual(self.executed, ["action-0"])
        self.assertEqual(results[-1].text, "当前没有待确认操作。")

    async def test_new_session_has_no_history_or_pending_run(self):
        old, _ = self._session()
        await old.send("上一会话请求的操作")
        model = ScriptedModel(replies=[AIMessage(content="新的会话")])
        new = HavenSession(model=model)
        self.assertNotEqual(old.thread_id, new.thread_id)
        self.assertEqual((await new.send("/approve")).text, "当前没有待确认操作。")
        await new.send("重新开始")
        self.assertNotIn("上一会话请求的操作", [m.content for m in model.seen[-1]])
        self.assertEqual(self.executed, [])

    async def test_failed_run_stops_session_without_retry(self):
        session, _ = self._session()
        await session.send("执行操作")
        with patch.object(
            session._agent, "ainvoke", AsyncMock(side_effect=RuntimeError("失败"))
        ) as call:
            self.assertTrue((await session.send("/approve")).failed)
            self.assertTrue((await session.send("/approve")).failed)
            self.assertEqual(call.await_count, 1)
        self.assertEqual(self.executed, [])

    async def test_single_turn_never_auto_approves(self):
        _, model = self._session()
        reply = await run_once("执行操作", model=model)
        self.assertIn("交互模式", reply)
        self.assertEqual(self.executed, [])

    async def test_cli_approves_then_exits(self):
        session, _ = self._session()
        output = io.StringIO()
        with (
            patch("haven.cli.HavenSession", return_value=session),
            patch("builtins.input", side_effect=["执行操作", "/approve", "/exit"]),
            redirect_stdout(output),
        ):
            await asyncio.to_thread(chat)
        self.assertIn("delete_my_data", output.getvalue())
        self.assertIn("操作已处理", output.getvalue())
        self.assertEqual(self.executed, ["action-0"])

    async def test_cli_exit_leaves_sensitive_tool_unexecuted(self):
        session, _ = self._session()
        with (
            patch("haven.cli.HavenSession", return_value=session),
            patch("builtins.input", side_effect=["执行操作", "/exit"]),
            redirect_stdout(io.StringIO()),
        ):
            await asyncio.to_thread(chat)
        self.assertEqual(self.executed, [])


if __name__ == "__main__":
    unittest.main()
