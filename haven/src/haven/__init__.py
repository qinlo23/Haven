"""健健公共接口；延迟加载，导入包不读取提示词、不创建模型或连接数据库。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from haven.session import HavenSession, SessionReply, create_haven, run_once

__all__ = ["HavenSession", "SessionReply", "create_haven", "run_once"]


def __getattr__(name: str) -> Any:
    """仅在访问公共会话接口时加载业务模块。"""
    if name in __all__:
        from haven import session

        value = getattr(session, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
