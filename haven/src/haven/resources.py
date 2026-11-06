"""统一行为规范的读取；源码保留唯一编辑源，wheel 使用打包资源。"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from haven.config.paths import source_root


def read_instructions(path: str | Path = "instructions.md") -> str:
    """读取行为规范；缺失、解码失败或空白均明确拒绝启动。"""
    source = source_root()
    try:
        if Path(path).is_absolute():
            text = Path(path).read_text(encoding="utf-8")
        elif source is not None:
            text = (source / path).read_text(encoding="utf-8")
        else:
            text = files("haven").joinpath("resources", str(path)).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("高血压 Agent 行为规范读取失败，已停止装配。") from exc
    if not text.strip():
        raise RuntimeError("高血压 Agent 行为规范为空，已停止装配。")
    return text
