"""通用 ReAct 模块回归；只使用模拟模型与模拟工具，无业务或网络依赖。"""

from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, StateSnapshot
from langsmith import tracing_context
from pydantic import Field

from haven.reasoning import ReactState, build_react_graph
from haven.reasoning.react import REJECTED_TOOL_REPLY, TOOL_FAILED_REPLY, InvalidReview

FALLBACK = "模拟降级回复"


class MockModel(BaseChatModel):
    """仅异步返回预设消息；捕获输入，验证循环和上下文。"""

    replies: list[AIMessage] = Field(default_factory=list)
    seen: list[list] = Field(default_factory=list)
    schemas: list[dict] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "react-offline-test"

    def bind_tools(self, tools, **kwargs):
        self.schemas = [item.tool_call_schema.model_json_schema() for item in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise AssertionError("测试必须异步执行")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(messages)
        if not self.replies:
            raise RuntimeError("模拟故障，包含不可输出的原始参数")
        return ChatResult(generations=[ChatGeneration(message=self.replies.pop(0))])


def calls(*items):
    """构造一个批次，每项依次为工具名、调用 ID 和参数值。"""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "id": call_id, "args": {"value": value}}
            for name, call_id, value in items
        ],
    )


class ReactTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.enterContext(tracing_context(enabled=False))
        self.events = []
        self.runtimes = []
        self.config = {"configurable": {"thread_id": str(uuid4())}, "recursion_limit": 100}

        @tool
        async def echo(value: str, runtime: ToolRuntime) -> str:
            """返回模拟输入，供普通工具测试使用。"""
            self.assertIsInstance(runtime, ToolRuntime)
            self.runtimes.append(runtime)
            self.events.append(f"echo:start:{value}")
            await asyncio.sleep(0)
            self.events.append(f"echo:end:{value}")
            return f"echo:{value}"

        @tool
        async def sensitive(value: str, runtime: ToolRuntime) -> str:
            """模拟需要人工批准的写入，不操作数据库。"""
            self.runtimes.append(runtime)
            self.events.append(f"sensitive:{value}")
            return "模拟写入完成"

        @tool
        async def broken(value: str) -> str:
            """模拟执行异常，不操作外部系统。"""
            self.events.append("broken")
            raise RuntimeError("不可输出的原始参数")

        self.tools = [echo, sensitive, broken]

    def graph(self, model, **kwargs):
        child = build_react_graph(
            model=model,
            tools=self.tools,
            system_prompt="模拟基础提示",
            fallback_reply=FALLBACK,
            approval_rules={"sensitive": True},
            **kwargs,
        )

        def entry(state):
            return {"model_calls": 0, "pending_tools": [], "tool_index": 0, "review_decisions": {}}

        parent = StateGraph(ReactState)
        parent.add_node("entry", entry)
        parent.add_node("business", child)
        parent.add_edge(START, "entry")
        parent.add_edge("entry", "business")
        parent.add_edge("business", END)
        return parent.compile(checkpointer=InMemorySaver())

    async def send(self, graph, text="模拟用户请求"):
        return await graph.ainvoke({"messages": [HumanMessage(content=text)]}, self.config)

    async def resume(self, graph, result, kind="approve"):
        return await graph.ainvoke(
            Command(
                resume={
                    item.id: {"decisions": [{"type": kind} for _ in item.value["action_requests"]]}
                    for item in result["__interrupt__"]
                }
            ),
            self.config,
        )

    async def test_direct_answer(self):
        model = MockModel(replies=[AIMessage(content="模拟回答")])
        result = await self.send(self.graph(model))
        self.assertEqual(result["messages"][-1].content, "模拟回答")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(self.events, [])

    async def test_tools_execute_serially_and_keep_original_batch(self):
        model = MockModel(
            replies=[calls(("echo", "a", "一"), ("echo", "b", "二")), AIMessage(content="结束")]
        )
        result = await self.send(self.graph(model))
        self.assertEqual(
            self.events, ["echo:start:一", "echo:end:一", "echo:start:二", "echo:end:二"]
        )
        self.assertEqual(len(result["messages"][1].tool_calls), 2)
        outputs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual([m.tool_call_id for m in outputs], ["a", "b"])
        self.assertEqual([m.content for m in outputs], ["echo:一", "echo:二"])
        self.assertEqual(self.runtimes[0].tool_call_id, "a")
        for schema in model.schemas:
            self.assertNotIn("runtime", schema["properties"])

    async def test_sensitive_batch_pauses_all_tools_before_approval(self):
        model = MockModel(
            replies=[
                calls(("echo", "a", "一"), ("sensitive", "b", "二")),
                AIMessage(content="结束"),
            ]
        )
        graph = self.graph(model)
        pending = await self.send(graph)
        self.assertEqual(self.events, [])
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(
            pending["__interrupt__"][0].value["action_requests"][0]["name"], "sensitive"
        )
        result = await self.resume(graph, pending)
        self.assertFalse(result.get("__interrupt__"))
        self.assertEqual(self.events, ["echo:start:一", "echo:end:一", "sensitive:二"])
        self.assertEqual(result["model_calls"], 2)

    async def test_rejection_only_skips_sensitive_calls(self):
        model = MockModel(
            replies=[
                calls(("sensitive", "a", "一"), ("echo", "b", "二")),
                AIMessage(content="结束"),
            ]
        )
        graph = self.graph(model)
        result = await self.resume(graph, await self.send(graph), "reject")
        outputs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(outputs[0].content, REJECTED_TOOL_REPLY)
        self.assertEqual(outputs[0].status, "error")
        self.assertEqual([m.tool_call_id for m in outputs], ["a", "b"])
        self.assertEqual(self.events, ["echo:start:二", "echo:end:二"])

    async def test_multiple_sensitive_decisions_match_frozen_order(self):
        model = MockModel(
            replies=[
                calls(("sensitive", "a", "一"), ("sensitive", "b", "二")),
                AIMessage(content="结束"),
            ]
        )
        graph = self.graph(model)
        pending = await self.send(graph)
        item = pending["__interrupt__"][0]
        result = await graph.ainvoke(
            Command(resume={item.id: {"decisions": [{"type": "reject"}, {"type": "approve"}]}}),
            self.config,
        )
        self.assertEqual(self.events, ["sensitive:二"])
        self.assertEqual(result["messages"][-1].content, "结束")

    async def test_invalid_review_never_executes(self):
        for response in (
            None,
            {},
            {"decisions": []},
            {"decisions": [{"type": "edit"}]},
            {"decisions": [{"type": "approve", "args": {}}]},
        ):
            with self.subTest(response=response):
                model = MockModel(replies=[calls(("sensitive", "a", "一"))])
                graph = self.graph(model)
                pending = await self.send(graph)
                item = pending["__interrupt__"][0]
                # None 作为中断值有特殊含义，改为另一个非法的普通值。
                value = "非法值" if response is None else response
                with self.assertRaises(InvalidReview):
                    await graph.ainvoke(Command(resume={item.id: value}), self.config)
                self.assertEqual(self.events, [])

    async def test_resume_does_not_replay_completed_earlier_tools(self):
        model = MockModel(
            replies=[
                calls(("echo", "a", "先执行")),
                calls(("sensitive", "b", "后确认")),
                AIMessage(content="结束"),
            ]
        )
        graph = self.graph(model)
        pending = await self.send(graph)
        self.assertEqual(self.events, ["echo:start:先执行", "echo:end:先执行"])
        result = await self.resume(graph, pending)
        self.assertEqual(self.events, ["echo:start:先执行", "echo:end:先执行", "sensitive:后确认"])
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(len(model.seen), 3)

    async def test_budget_ends_after_eighth_tool_observation(self):
        model = MockModel(replies=[calls(("echo", f"a-{i}", str(i))) for i in range(9)])
        result = await self.send(self.graph(model))
        self.assertEqual(len(model.seen), 8)
        self.assertEqual(result["model_calls"], 8)
        self.assertEqual(result["messages"][-1].content, FALLBACK)
        self.assertEqual(len([m for m in result["messages"] if isinstance(m, ToolMessage)]), 8)
        self.assertEqual(result["pending_tools"], [])

    async def test_approval_does_not_reset_budget(self):
        model = MockModel(replies=[calls(("sensitive", "a", "一"))])
        graph = self.graph(model, max_model_calls=1)
        result = await self.resume(graph, await self.send(graph))
        self.assertEqual(self.events, ["sensitive:一"])
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(result["messages"][-1].content, FALLBACK)

    async def test_new_request_resets_budget_and_preserves_history(self):
        model = MockModel(replies=[AIMessage(content="第一次"), AIMessage(content="第二次")])
        graph = self.graph(model, max_model_calls=1)
        await self.send(graph, "第一条请求")
        result = await self.send(graph, "第二条请求")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["messages"][-1].content, "第二次")
        self.assertIn("第一条请求", [m.content for m in model.seen[-1]])

    async def test_model_error_is_sanitized_and_counted(self):
        result = await self.send(self.graph(MockModel()))
        self.assertEqual(result["messages"][-1].content, FALLBACK)
        self.assertEqual(result["model_calls"], 1)

    async def test_tool_error_is_sanitized_without_retry(self):
        model = MockModel(replies=[calls(("broken", "a", "参数")), AIMessage(content="结束")])
        result = await self.send(self.graph(model))
        output = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(output.content, TOOL_FAILED_REPLY)
        self.assertEqual(output.status, "error")
        self.assertEqual(self.events, ["broken"])

    async def test_unknown_tool_and_invalid_arguments_return_paired_errors(self):
        for message in (
            calls(("missing", "a", "参数")),
            AIMessage(content="", tool_calls=[{"name": "echo", "id": "b", "args": {}}]),
        ):
            model = MockModel(replies=[message, AIMessage(content="结束")])
            result = await self.send(self.graph(model))
            output = next(m for m in result["messages"] if isinstance(m, ToolMessage))
            self.assertEqual(output.status, "error")
            self.assertEqual(output.tool_call_id, message.tool_calls[0]["id"])
        self.assertEqual(self.events, [])

    async def test_missing_or_duplicate_call_ids_drop_entire_batch(self):
        for message in (
            calls(("echo", None, "一")),
            calls(("echo", "a", "一"), ("echo", "a", "二")),
        ):
            model = MockModel(replies=[message])
            result = await self.send(self.graph(model))
            self.assertEqual(result["messages"][-1].content, FALLBACK)
            self.assertFalse(
                any(isinstance(m, AIMessage) and m.tool_calls for m in result["messages"])
            )
        self.assertEqual(self.events, [])

    async def test_model_middleware_order_and_summary_not_in_history(self):
        events = []

        class Layer(AgentMiddleware):
            def __init__(self, label):
                self.label = label

            async def awrap_model_call(self, request, handler):
                events.append(f"{self.label}:before")
                message = SystemMessage(content=request.system_message.text + f"/{self.label}")
                result = await handler(request.override(system_message=message))
                events.append(f"{self.label}:after")
                return result

        model = MockModel(replies=[AIMessage(content="结束")])
        result = await self.send(self.graph(model, model_middleware=[Layer("外"), Layer("摘要")]))
        self.assertEqual(events, ["外:before", "摘要:before", "摘要:after", "外:after"])
        self.assertEqual(model.seen[0][0].text, "模拟基础提示/外/摘要")
        self.assertFalse(any(isinstance(m, SystemMessage) for m in result["messages"]))

    async def test_tool_middleware_can_short_circuit(self):
        class Deny(AgentMiddleware):
            async def awrap_tool_call(self, request, handler):
                return ToolMessage(
                    content="模拟策略拒绝", tool_call_id=request.tool_call["id"], status="error"
                )

        model = MockModel(replies=[calls(("echo", "a", "一")), AIMessage(content="结束")])
        result = await self.send(self.graph(model, tool_middleware=[Deny()]))
        output = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(output.content, "模拟策略拒绝")
        self.assertEqual(self.events, [])

    async def test_model_middleware_can_short_circuit_without_using_budget(self):
        class Reply(AgentMiddleware):
            async def awrap_model_call(self, request, handler):
                return ModelResponse(result=[AIMessage(content="确定性回复")])

        model = MockModel()
        result = await self.send(self.graph(model, model_middleware=[Reply()]))
        self.assertEqual(result["messages"][-1].content, "确定性回复")
        self.assertEqual(result["model_calls"], 0)
        self.assertEqual(model.seen, [])

    async def test_middleware_cannot_exceed_model_budget(self):
        class Twice(AgentMiddleware):
            async def awrap_model_call(self, request, handler):
                await handler(request)
                return await handler(request)

        model = MockModel(replies=[AIMessage(content="第一次"), AIMessage(content="第二次")])
        result = await self.send(self.graph(model, model_middleware=[Twice()], max_model_calls=1))
        self.assertEqual(len(model.seen), 1)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["messages"][-1].content, FALLBACK)

    async def test_tool_middleware_cannot_replace_approved_call(self):
        class Replace(AgentMiddleware):
            async def awrap_tool_call(self, request, handler):
                return await handler(
                    request.override(
                        tool_call={
                            **request.tool_call,
                            "name": "sensitive",
                            "args": {"value": "替换参数"},
                        }
                    )
                )

        model = MockModel(replies=[calls(("echo", "a", "原始参数")), AIMessage(content="结束")])
        result = await self.send(self.graph(model, tool_middleware=[Replace()]))
        output = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(output.content, REJECTED_TOOL_REPLY)
        self.assertEqual(output.tool_call_id, "a")
        self.assertEqual(self.events, [])

    async def test_tool_command_cannot_route_parent_graph(self):
        @tool
        async def route(runtime: ToolRuntime) -> Command:
            """模拟返回不受支持的图路由，不产生业务写入。"""
            return Command(
                update={
                    "messages": [ToolMessage(content="非法路由", tool_call_id=runtime.tool_call_id)]
                },
                goto=END,
            )

        self.tools.append(route)
        model = MockModel(
            replies=[
                AIMessage(content="", tool_calls=[{"name": "route", "id": "a", "args": {}}]),
                AIMessage(content="仍然由原图结束"),
            ]
        )
        result = await self.send(self.graph(model))
        output = next(m for m in result["messages"] if isinstance(m, ToolMessage))
        self.assertEqual(output.content, TOOL_FAILED_REPLY)
        self.assertEqual(result["messages"][-1].content, "仍然由原图结束")

    async def test_tool_steps_are_checkpointed_separately(self):
        model = MockModel(
            replies=[
                calls(("sensitive", "a", "一"), ("echo", "b", "二")),
                AIMessage(content="结束"),
            ]
        )
        graph = self.graph(model)
        pending = await self.send(graph)
        snapshot = await graph.aget_state(self.config, subgraphs=True)
        child_state = snapshot.tasks[0].state
        assert isinstance(child_state, StateSnapshot)
        child_config = deepcopy(child_state.config)
        child_config["configurable"].pop("checkpoint_id", None)
        child_config["configurable"].pop("checkpoint_map", None)
        await self.resume(graph, pending)
        history = [state async for state in graph.aget_state_history(child_config)]
        intermediate = [state for state in history if state.values.get("tool_index") == 1]
        self.assertTrue(intermediate)
        messages = intermediate[0].values["messages"]
        self.assertEqual([m.tool_call_id for m in messages if isinstance(m, ToolMessage)], ["a"])

    def test_invalid_factory_configuration_fails_before_execution(self):
        cases: list[dict[str, Any]] = [
            {"max_model_calls": 0},
            {"max_model_calls": True},
            {"approval_rules": {"typo": True}},
        ]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                build_react_graph(
                    model=MockModel(),
                    tools=self.tools,
                    system_prompt="提示",
                    fallback_reply=FALLBACK,
                    **extra,
                )


if __name__ == "__main__":
    unittest.main()
