"""工具共享辅助：调用者识别、同意闸门、共用取数口径、确定性时间解析。

供 `tools/` 与 `middleware/` 双方使用：同一份数据（同意状态、近 7 天血压）
必须只有一处查询，各写一份迟早会分叉成两套数字。

日期类解析（ISO / 中文年月日 / 纯年份）在 `application/onboarding.parse_date`
—— 那是档案字段校验的唯一真相源，本模块不再重复实现一份。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from langchain.tools import ToolRuntime
from langchain_core.tools import InjectedToolArg
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.messages import MSG
from safety.degradation import DB_DEGRADED_RESPONSE
from storage.database import caller_user_id
from storage.models import BloodPressure, ConsentRecord

#: 单租户部署下，走到这里只有一种原因：调用者不是本部署服务的本人。
#: 真正的原因（期望/实际 user id）在服务端日志里 —— 不回显给调用者。
NO_IDENTITY_REPLY = MSG.identity_required

# 可空默认值供直接调用；InjectedToolArg 保证模型 schema 不暴露 runtime。
OptionalToolRuntime = Annotated[ToolRuntime | None, InjectedToolArg]

#: 允许的时钟偏差：设备时钟略快于服务器时不算“未来”。
_FUTURE_TOLERANCE = timedelta(minutes=5)

#: 「近 7 天」的窗口与读取上限。
SEVEN_DAY_WINDOW = timedelta(days=7)
MAX_SEVEN_DAY_RECORDS = 500


# ── 工具 runtime 参数的注解约定 ──────────────────────────────────────────
# 用 langgraph 原生 ToolRuntime 注解，而不是 MDA 文档推荐的
# ManagedDeepAgentRuntime：ToolNode 注入的是原生 ToolRuntime 实例，而参数
# 校验（pydantic）发生在 MDA 的 seam 把 runtime 换成带 identity 的代理
# **之前** —— 注解成 ManagedDeepAgentRuntime 会因「不是该类型实例」校验
# 失败，工具只报「Error invoking tool …」（错误详情按“注入参数”被过滤，
# 显示为空）且不落任何数据（langchain-core 1.4/1.5/1.6 均可复现）。
# OptionalToolRuntime 保留原生 ToolRuntime 类型并显式标记注入，
# 不出现在模型可见的工具 schema 里；MDA seam 的代理仍可用于身份审计。
def uid_of(runtime: ToolRuntime | None) -> str | None:
    return caller_user_id(runtime)


async def has_consented(session: AsyncSession, uid: str) -> bool:
    stmt = select(ConsentRecord).where(ConsentRecord.user_id == uid).limit(1)
    result = await session.execute(stmt)
    return result.scalars().first() is not None


async def seven_day_records(session: AsyncSession, uid: str) -> list[BloodPressure]:
    """该用户近 7 天的血压记录（时间升序）。

    这是「近 7 天」的**唯一取数口径** —— `get_seven_day_trend`（用户向）与
    记忆注入（模型向）共用，两处渲染出的记录数/均值/最高/达标率才会逐项
    一致。上限是成本兜底，不是业务规则：单列索引下按时间倒序扫。
    """
    stmt = (
        select(BloodPressure)
        .where(
            BloodPressure.user_id == uid,
            BloodPressure.measured_at >= datetime.now(UTC) - SEVEN_DAY_WINDOW,
        )
        .order_by(BloodPressure.measured_at.asc())
        .limit(MAX_SEVEN_DAY_RECORDS)
    )
    return list((await session.execute(stmt)).scalars().all())


def parse_measured_at(text: str | None) -> datetime | None:
    """解析测量时间；date-only 视为当天 00:00 UTC；返回带时区 UTC。"""
    if not text:
        return datetime.now(UTC)
    text = text.strip()
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def is_future(value: datetime) -> bool:
    return value > datetime.now(UTC) + _FUTURE_TOLERANCE


def degraded() -> str:
    return DB_DEGRADED_RESPONSE
