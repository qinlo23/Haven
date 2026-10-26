"""记忆注入 —— 每次模型调用前，把该用户的确定性数据摘要并入 system prompt。

B2 / S1.6 / S9.2：「'思考'前读取该用户画像与近期体征注入决策上下文」。
摘要由 `application/memory.py` 纯组装（对权威表的**只读视图**，不落库、
不进线程历史），本模块只负责取数、降级与注入位置。

四条硬约束（改这个文件时别破坏）：

1. **只读**。注入发生在每一次模型调用（含 ReAct 的每个迭代），这里不能有
   任何写入：过期待确认行只判不删，陈旧草稿只跳过不清理。
2. **失败即降级**。取数或组装出任何异常都必须在这里吞掉，然后照常调用
   `handler(request)`（S9.9「记忆检索失败 → 仅使用当前对话上下文」）。
   不能指望外层兜底：`haven_output_safety` 在更外层、用 `except Exception`
   返回模型降级文案（`middleware/output_safety.py:44-48`），从这里抛出去的
   异常会被误判成"模型故障"，用户会听到一句关于模型的假话。
3. **无跨用户状态**。每次调用现读现组装，禁止任何模块级缓存
   （REQ-005 §4.3：禁止跨用户共享状态）。
4. **非本人不注入**。单租户部署下 `uid is None` 只可能是"调用者不是本人"
   （见 `storage/database.py:caller_user_id`）；此时连"未同意"块也不能注入 ——
   **注入块里就是本人的健康数据**，一个字节都不该给非本人看。各工具已有
   `NO_IDENTITY_REPLY` 兜底。

嵌套位置（外→内）：`haven_output_safety` → **`haven_memory_context`** →
`mda-channel-response-format` → prompt-caching → HumanInTheLoop。即：在安全
中间件的**内侧**，但不在最内层。今天不丢东西（内侧两个中间件都是往 system
prompt **追加**内容块，不是重写），将来往内侧再加中间件时要重新确认这一点。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import SystemMessage
from sqlalchemy import select

from application.hypertension.confirmation import pending_expired
from application.memory import build_bundle, format_memory_block
from application.hypertension.trends import calculate_seven_day_trend
from messages.instructions import system_prompt as haven_system_prompt
from storage.database import caller_user_id, session_scope
from storage.models import (
    DRAFT_INJECTION_WINDOW,
    DiseaseRecord,
    HealthProfile,
    OnboardingDraft,
    PendingBpConfirmation,
)
from tools._helpers import has_consented, seven_day_records

#: 取记忆的单次时间预算（秒）。S1.6 要求检索 < 200ms，本地 SQLite 是 O(ms)
#: 级，留 1 秒是给 PostgreSQL 远端抖动的余量 —— `session_scope` 自身没有
#: 超时，"捕获任何异常"覆盖不了**挂起**，挂起会把模型回复一起拖住。
MEMORY_READ_TIMEOUT = 1.0


def _draft_is_stale(updated_at: datetime, now: datetime) -> bool:
    """草稿是否已超出注入窗口 —— 太久没动的进度不再注入，免得模型对几个月
    前的半截问答"接着问"。

    SQLite 读回的时间是 naive，按 UTC 归一化后再比较（口径与
    `application.hypertension.confirmation.pending_expired` 一致：那边管过期，这边管窗口）。
    """
    moment = (
        updated_at if updated_at.tzinfo is not None else updated_at.replace(tzinfo=UTC)
    )
    return moment < now - DRAFT_INJECTION_WINDOW


def _inject_blocks(request: ModelRequest, *blocks: str) -> ModelRequest:
    """把若干文本块作为**独立内容块**追加到 system prompt。

    每次调用都以 ``content_blocks`` 形式追加，不压平已有中间件设下的
    ``cache_control`` 标记。``request.override`` 基于未修改的闭包
    ``system_message`` 重建请求，注入块不会在线程历史里累积/重复。

    过渡期：首块来自 ``messages.instructions.system_prompt.build()``，
    显式替代 MDA 隐性注入的 system prompt（来自 Context Hub 的
    ``instructions.md`` + ``_memory_instructions.py`` 的
    ``<managed_memory_policy>``）。MDA 的隐性注入仍在运行，但你显式
    追加的块包含完整的 instructions + memory policy，模型只看到一份
    完整的系统提示。
    """
    existing = getattr(request, "system_message", None)
    current: list[Any] = list(getattr(existing, "content_blocks", None) or [])
    for block in blocks:
        current.append({"type": "text", "text": block})
    return request.override(system_message=SystemMessage(content_blocks=current))


async def _load_bundle(session, uid: str):
    """只读取数（全部带 `user_id == uid` 过滤，REQ-005 §4.3）并组装摘要。"""
    consented = await has_consented(session, uid)
    if not consented:
        # 未同意 → 只输出红线块，绝不把任何健康数据送进上下文。
        return build_bundle(consented=False)

    now = datetime.now(UTC)

    stmt = select(HealthProfile).where(HealthProfile.user_id == uid).limit(1)
    profile = (await session.execute(stmt)).scalars().first()

    stmt = select(DiseaseRecord).where(DiseaseRecord.user_id == uid)
    diseases = (await session.execute(stmt)).scalars().all()

    # 与 `get_seven_day_trend` 共用同一取数口径 —— 注入的均值/达标率必须与
    # 工具输出逐项一致，各写一份查询迟早会分叉成两套数字。
    records = await seven_day_records(session, uid)

    stmt = (
        select(PendingBpConfirmation)
        .where(PendingBpConfirmation.user_id == uid)
        .limit(1)
    )
    pending = (await session.execute(stmt)).scalars().first()
    if pending is not None and pending_expired(pending.expires_at, now=now):
        pending = None  # 只判不删：注入是只读路径，清理留给工具层。

    stmt = select(OnboardingDraft).where(OnboardingDraft.user_id == uid).limit(1)
    draft = (await session.execute(stmt)).scalars().first()
    if draft is not None and _draft_is_stale(draft.updated_at, now):
        draft = None

    # 「最近一次」取的是**近 7 天内**的最近一条（与趋势同一个窗口）。窗口外
    # 的记录不注入 —— 注入块里没有绝对时间戳可比对，把三周前的数值说成
    # "最近一次"是误导；需要更早的数据时由工具去查。
    last_bp = records[-1] if records else None

    return build_bundle(
        consented=True,
        profile=profile,
        diseases=tuple(diseases),
        trend=calculate_seven_day_trend(records),
        last_bp=last_bp,
        pending=pending,
        draft=draft,
        now=now,
    )


async def _memory_block(uid: str) -> str:
    async with session_scope() as session:
        return format_memory_block(await _load_bundle(session, uid))


class MemoryInjectionMiddleware(AgentMiddleware):
    """该用户数据的确定性摘要 → system prompt（只读，失败静默）。"""

    #: 名字必须唯一：deepagents 按 `.name` 合并作者中间件，撞名会顶掉别人的。
    @property
    def name(self) -> str:
        return "haven_memory_context"

    async def awrap_model_call(self, request: ModelRequest, handler):
        uid = caller_user_id(getattr(request, "runtime", None))

        # 始终注入显式的 system prompt（含 instructions + memory policy），
        # 先于记忆块。MDA 的隐性注入仍在运行，但你显式的块包含完整的系统提示。
        new_request = _inject_blocks(request, haven_system_prompt.build())

        if uid is None:
            # 非本人调用者：只注入 system prompt，不注入记忆块。
            # 记忆块里就是本人的健康数据，一个字节都不该给非本人看。
            return await handler(new_request)

        # try 只包住"取数 + 组装"：handler 必须留在外面，否则模型自身的异常
        # 会被这里吞掉，output_safety 的降级文案就永远发不出来了。
        try:
            block = await asyncio.wait_for(_memory_block(uid), MEMORY_READ_TIMEOUT)
        except Exception:  # noqa: BLE001 —— 见模块头第 2 条
            block = None
        if block:
            new_request = _inject_blocks(request, haven_system_prompt.build(), block)
        return await handler(new_request)

    def wrap_model_call(self, request: ModelRequest, handler):
        """同步路径**不注入** —— `session_scope` 只有异步版。

        生产形态（LangGraph Agent Server）全程异步，`langgraph_api` 里没有
        任何 `.invoke(` 调用点，`mda dev` 同源，所以这条路径实际不跑；表现是
        "静默无记忆"而非报错。将来若真要补齐，参照 MDA 自己的同步桥
        `.mda/build/__runtime__/managed_deepagents/_memory/async_util.py`。
        """
        return handler(request)


memory_context_middleware = MemoryInjectionMiddleware()
