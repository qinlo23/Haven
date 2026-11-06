"""MCP 服务器清单（`config/mcp.json`）的**标准库**读取器（单一解析处）。

MDA 0.10 在仅安装 SDK 的提取环境中导入根目录 `tools/mcp.py`，
此时项目包尚未安装。声明入口用标准库 run_path 执行本模块，因此这里必须
保持独立、只依赖标准库；`haven` 与 `haven.config` 初始化也保持轻量。
路径函数与清单解析集中于此，供标准包导入与平台提取共用。

清单是包内文件（模块相对路径，不依赖 cwd），随项目提交、随构建逐字拷贝
进部署产物。规则：

- 文件缺失 / 空白 / `{}` → 不启用任何服务器（MCP 关闭，不报错）；
- JSON 写错、顶层不是对象、某服务器不是对象 → **大声报错**（配置错误必须
  在构建/启动时暴露）；没配置、或某个服务器没给非空 `include_tools`
  白名单，则不启用该服务器；
- 字符串值支持 `${VAR}` 插值，按 **进程环境 → 项目根 `.env`** 依次解析；
  引用未设置（或为空）的变量 → 大声报错。密钥只放 `.env`，不进仓库。
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import cast


def source_root() -> Path | None:
    """仅按实际源码位置识别项目；不从启动目录寻找同名仓库。"""
    package = Path(__file__).resolve().parents[1]
    if package.parent.name == "src":
        candidate = package.parent.parent
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def env_file() -> Path:
    """源码使用项目根 .env，wheel 安装使用启动目录 .env。"""
    return (source_root() or Path.cwd()) / ".env"


#: MCP 清单：包内文件，模块相对定位（三种运行方式的 cwd 都成立）。
_MCP_FILE = Path(__file__).resolve().parent / "mcp.json"
#: 插值回落用的项目根 .env（进程环境优先，见 `_lookup`）。
_ENV_FILE = env_file()

_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@lru_cache(maxsize=1)
def _dotenv() -> dict[str, str]:
    """项目根 `.env` 的朴素解析（仅供 `${VAR}` 回落；读不到 → {}）。

    只处理 `KEY=value` 单行、跳过空行与 `#` 注释、剥成对引号 —— 与
    pydantic-settings 相比刻意保持简单：这里只服务插值取值。
    """
    values: dict[str, str] = {}
    try:
        text = _ENV_FILE.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def _lookup(name: str) -> str | None:
    """插值取值：进程环境优先（空串按未设置处理），其次项目根 `.env`。"""
    if os.environ.get(name):
        return os.environ[name]
    return _dotenv().get(name) or None


def _substitute(node: object) -> object:
    """递归替换字符串值里的 `${VAR}`；键保持字面量（服务器名不参与插值）。"""
    if isinstance(node, str):

        def replace(match: re.Match[str]) -> str:
            value = _lookup(match.group(1))
            if value is None:
                raise ValueError(
                    f"config/mcp.json 引用了未设置的环境变量 ${{{match.group(1)}}}"
                )
            return value

        return _VAR_PATTERN.sub(replace, node)
    if isinstance(node, dict):
        return {key: _substitute(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_substitute(item) for item in node]
    return node


@lru_cache(maxsize=1)
def load_mcp_servers() -> dict[str, dict[str, object]]:
    """已启用的服务器：``{服务器名: 原始配置字典}``（白名单为空的跳过）。

    结果带缓存（工具调用路径上零重复 IO）；改 `mcp.json` / `.env` 后需重启
    进程才生效。
    """
    try:
        text = _MCP_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)  # 写错 JSON：在这里直接报错
    except json.JSONDecodeError as exc:
        raise ValueError(f"config/mcp.json JSON 解析失败：{exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("config/mcp.json 必须是 JSON 对象（服务器名 -> 配置）")
    # 顶层已校验为 dict，_substitute 对 dict 返回 dict；cast 仅用于类型收窄。
    substituted = cast("dict[str, object]", _substitute(parsed))
    servers: dict[str, dict[str, object]] = {}
    for name, config in substituted.items():
        if not isinstance(config, dict):
            raise ValueError(f"config/mcp.json 中 {name!r} 的配置必须是 JSON 对象")
        include = config.get("include_tools")
        if isinstance(include, list) and include:
            servers[str(name)] = config
    return servers
