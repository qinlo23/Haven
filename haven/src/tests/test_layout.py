"""布局迁移回归：隔离包导入、配置来源、提示词和新版 MCP 策略。"""

from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from langchain.agents.middleware.types import ToolCallRequest
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langsmith import tracing_context
from managed_deepagents import ManagedToolRuntime, define_deep_agent
from managed_deepagents.runtime import compile_managed_agent_async
from test_react import MockModel

from haven.application.messages import MSG
from haven.config import mcp, paths
from haven.config.settings import McpServerSettings, Settings
from haven.middleware.mcp_policy import McpPolicyMiddleware
from haven.resources import read_instructions
from haven.safety.degradation import MCP_DEGRADED_RESPONSE, MCP_DENIED_RESPONSE
from haven.storage.database import caller_user_id


class LayoutTests(unittest.TestCase):
    """不接触真实密钥、数据库或外部服务。"""

    def test_import_from_other_directory_is_lightweight(self):
        script = (
            "import sys; import haven; import haven.config.mcp; "
            "assert 'haven.session' not in sys.modules; "
            "assert 'haven.messages.instructions' not in sys.modules; "
            "assert 'pydantic' not in sys.modules; "
            "assert 'sqlalchemy' not in sys.modules; "
            "assert 'langchain' not in sys.modules"
        )
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([sys.executable, "-I", "-c", script], cwd=directory, check=True)

    def test_source_and_installed_env_locations(self):
        self.assertEqual(paths.env_file(), Path(__file__).resolve().parents[1] / ".env")
        with patch("haven.config.mcp.source_root", return_value=None):
            self.assertEqual(paths.env_file(), Path.cwd() / ".env")

    def test_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            dotenv = Path(directory) / ".env"
            dotenv.write_text("HAVEN_OWNER_ID=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"HAVEN_OWNER_ID": "from-process"}):
                self.assertEqual(Settings(_env_file=dotenv).owner_id, "from-process")

    def test_prompt_source_and_packaged_resource_match(self):
        expected = (Path(__file__).resolve().parents[1] / "instructions.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(read_instructions(), expected)
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("haven.resources.source_root", return_value=None),
            patch("haven.resources.files", return_value=Path(directory)),
        ):
            resource = Path(directory) / "resources/instructions.md"
            resource.parent.mkdir()
            resource.write_text(expected, encoding="utf-8")
            self.assertEqual(read_instructions(), expected)
            resource.write_text(" ", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_instructions()
            resource.unlink()
            with self.assertRaises(RuntimeError):
                read_instructions()

    def test_mcp_disabled_and_environment_interpolation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "mcp.json"
            dotenv = Path(directory) / ".env"
            dotenv.write_text("HAVEN_TEST_MCP_TOKEN=file-placeholder\n", encoding="utf-8")
            with (
                patch.object(mcp, "_MCP_FILE", config),
                patch.object(mcp, "_ENV_FILE", dotenv),
                patch.dict(os.environ, {"HAVEN_TEST_MCP_TOKEN": "process-placeholder"}),
            ):
                self.addCleanup(mcp.load_mcp_servers.cache_clear)
                self.addCleanup(mcp._dotenv.cache_clear)
                mcp.load_mcp_servers.cache_clear()
                mcp._dotenv.cache_clear()
                self.assertEqual(mcp.load_mcp_servers(), {})
                config.write_text('{"docs": {"include_tools": []}}', encoding="utf-8")
                mcp.load_mcp_servers.cache_clear()
                self.assertEqual(mcp.load_mcp_servers(), {})
                config.write_text(
                    '{"docs": {"include_tools": ["search"], '
                    '"headers": {"Authorization": "${HAVEN_TEST_MCP_TOKEN}"}}}',
                    encoding="utf-8",
                )
                mcp.load_mcp_servers.cache_clear()
                self.assertEqual(
                    cast(dict[str, str], mcp.load_mcp_servers()["docs"]["headers"])["Authorization"],
                    "process-placeholder",
                )

    def test_mda_declaration_uses_same_standalone_config_loader(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "src/haven/config"
            config.mkdir(parents=True)
            (root / "tools").mkdir()
            (root / "pyproject.toml").write_text("", encoding="utf-8")
            shutil.copy2(project / "tools/mcp.py", root / "tools/mcp.py")
            shutil.copy2(project / "src/haven/config/mcp.py", config / "mcp.py")
            (config / "mcp.json").write_text(
                '{"docs": {"url": "https://example.invalid/mcp", '
                '"include_tools": ["search"], "default_tool_timeout": 20}}',
                encoding="utf-8",
            )
            declaration = runpy.run_path(str(root / "tools/mcp.py"))["mcp"]
            server = declaration.config["servers"]["docs"]
            self.assertEqual(server["include_tools"], ["search"])
            self.assertEqual(server["transport"], "http")
            self.assertNotIn("default_tool_timeout", server)
            self.assertFalse(declaration.config["throw_on_load_error"])

    def test_new_managed_principal_can_only_veto_owner(self):
        with patch("haven.storage.database.get_settings", return_value=SimpleNamespace(owner_id="owner")):
            for kind, actor, expected in (
                ("service", "platform", "owner"),
                ("person", "owner", "owner"),
                ("person", "someone-else", None),
            ):
                runtime = SimpleNamespace(
                    server_info=SimpleNamespace(principal=SimpleNamespace(kind=kind, id=actor))
                )
                self.assertEqual(caller_user_id(runtime), expected)


class McpUpgradeTests(unittest.IsolatedAsyncioTestCase):
    """新版单下划线工具名仍受白名单、审计及失败降级保护。"""

    async def test_native_names_allow_deny_and_degrade(self):
        policy = McpPolicyMiddleware()
        settings = SimpleNamespace(
            mcp_servers={
                "docs": McpServerSettings(url="https://example.invalid/mcp", include_tools=["search"])
            }
        )
        with (
            patch("haven.middleware.mcp_policy.get_settings", return_value=settings),
            patch.object(policy, "_audit", AsyncMock()) as audit,
        ):
            for name, expected, calls, failed in (
                ("docs_search", "已查询", 1, False),
                ("docs_write", MCP_DENIED_RESPONSE, 0, False),
                ("docs__search", MCP_DENIED_RESPONSE, 0, False),
                ("docs_search", MCP_DEGRADED_RESPONSE, 1, True),
            ):
                request = ToolCallRequest(
                    tool_call={"name": name, "args": {}, "id": "test", "type": "tool_call"},
                    tool=None,
                    state={},
                    runtime=SimpleNamespace(),
                )
                handler = AsyncMock(
                    return_value=ToolMessage(content="已查询", tool_call_id="test"),
                    side_effect=RuntimeError("模拟远端失败") if failed else None,
                )
                reply = await policy.awrap_tool_call(request, handler)
                self.assertEqual(reply.content, expected)
                self.assertEqual(handler.await_count, calls)
            self.assertEqual(audit.await_count, 4)

    async def test_managed_runtime_injection_accepts_native_tool_annotation(self):
        @tool
        async def runtime_probe(runtime: ToolRuntime) -> str:
            """离线检查托管运行时注入，不访问任何业务数据。"""
            return "注入成功" if isinstance(runtime, ManagedToolRuntime) else "注入失败"

        model = MockModel(
            replies=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "runtime_probe", "args": {}, "id": "probe"}],
                ),
                AIMessage(content="检查完成"),
            ]
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            tracing_context(enabled=False),
            patch(
                "haven.middleware.mcp_policy.get_settings",
                return_value=SimpleNamespace(mcp_servers={}),
            ),
        ):
            graph = await compile_managed_agent_async(
                define_deep_agent(
                    name="haven-runtime-test",
                    model=model,
                    tools=[runtime_probe],
                    middleware=[McpPolicyMiddleware()],
                ),
                context_root=directory,
                system_prompt="离线运行时检查。",
                report_telemetry=False,
            )
            result = await graph.ainvoke({"messages": [HumanMessage(content="检查注入")]})
        replies = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        self.assertEqual([message.content for message in replies], ["注入成功"])
        self.assertEqual(result["messages"][-1].content, "检查完成")
        probe_schema = next(schema for schema in model.schemas if schema["title"] == "runtime_probe")
        self.assertNotIn("runtime", probe_schema["properties"])

    async def test_generated_general_purpose_graph_keeps_command_safety_chain(self):
        model = MockModel()
        with (
            patch("haven.config.settings.get_settings", return_value=SimpleNamespace(chat_model=model)),
            tempfile.TemporaryDirectory() as directory,
            tracing_context(enabled=False),
        ):
            entry = runpy.run_path(str(Path(__file__).resolve().parents[1] / "agent.py"))["agent"]
            graph = await compile_managed_agent_async(
                entry,
                context_root=directory,
                system_prompt=read_instructions(),
                report_telemetry=False,
                async_subagent="general-purpose",
            )
            result = await graph.ainvoke({"messages": [HumanMessage(content="/hello")]})
        self.assertEqual(result["messages"][-1].content, MSG.greeting)
        self.assertEqual(model.seen, [])
