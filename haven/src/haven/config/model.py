"""模型配置（`config/model.yaml`）的加载器。

与 `config/mcp.py` 不同：本模块只被 `settings.py` 消费（pydantic 环境），
不需要标准库约束，直接使用 PyYAML。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

_MODEL_FILE = Path(__file__).resolve().parent / "model.yaml"
#: 默认模型（MDA 格式 <provider>:<model>）
DEFAULT_AGENT_MODEL = "deepseek:deepseek-v4-flash"


def _load_raw() -> dict[str, Any]:
    """加载 model.yaml 原始数据；文件缺失/空/非 dict → 返回 {}。"""
    try:
        text = _MODEL_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    if not text.strip():
        return {}
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items() if v is not None}


@lru_cache(maxsize=1)
def load_model_params() -> dict[str, Any]:
    """加载推理参数（`model.params` 子节点）；文件缺失或空 → 返回 {}（全部走默认值）。"""
    raw = _load_raw()
    model_node = raw.get("model")
    if isinstance(model_node, dict):
        params = model_node.get("params")
        if isinstance(params, dict):
            return {str(k): v for k, v in params.items() if v is not None}
    return {}


@lru_cache(maxsize=1)
def load_model_name() -> str:
    """加载模型名称（`model.name`）；YAML 未写或缺失 → 返回 DEFAULT_AGENT_MODEL。"""
    raw = _load_raw()
    model_node = raw.get("model")
    if isinstance(model_node, dict):
        name = model_node.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return DEFAULT_AGENT_MODEL


class AgentModelSettings(BaseModel):
    """模型推理参数 —— 从 `config/model.yaml` 加载，代码默认值兜底。

    所有参数均可被 `config/model.yaml` 覆盖；缺项/文件缺失则取此处的默认值。
    与 `.env` 不同：这些是产品语义参数（医疗场景调参），不是部署参数，
    走 YAML 而非环境变量是为了版本可控、代码评审可追踪。
    """

    temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=128000)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    frequency_penalty: float = Field(default=0.3, ge=-2.0, le=2.0)
    presence_penalty: float = Field(default=0.1, ge=-2.0, le=2.0)
    timeout: float = Field(default=60.0, ge=1.0)
    max_retries: int = Field(default=2, ge=0, le=10)
    max_concurrent_requests: int = Field(default=5, ge=1, le=100)
    requests_per_minute: int = Field(default=60, ge=0, le=10000)
    rate_limit_check_interval: float = Field(default=0.1, ge=0.01, le=1.0)
