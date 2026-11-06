"""MDA 0.10 原生 MCP 声明；空配置关闭，远端失败由策略层降级。"""

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

from managed_deepagents import RemoteMcpServerConfig, define_mcp

# MDA 的声明提取环境只安装 SDK、尚未安装项目包。直接执行同一份纯标准库
# 配置加载器，不引入业务依赖，不修改 sys.path，也不复制配置解析逻辑。
_config_file = Path(__file__).resolve().parents[1] / "src/haven/config/mcp.py"
_load_servers = cast(
    Callable[[], dict[str, dict[str, object]]],
    run_path(str(_config_file))["load_mcp_servers"],
)

# 新版使用单下划线前缀；旧 default_tool_timeout 不属于新版声明字段。
_servers = {
    name: cast(
        RemoteMcpServerConfig,
        {
            **{key: value for key, value in config.items() if key != "default_tool_timeout"},
            "transport": config.get("transport", "http"),
        },
    )
    for name, config in _load_servers().items()
}

mcp = define_mcp(servers=_servers, throw_on_load_error=False) if _servers else None
