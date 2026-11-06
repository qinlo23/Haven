"""审计辅助 —— 去标识主体键 + 追加写事件。

审计是追加写且独立于业务表；删除数据时审计行保留（仅含去标识键与事件摘要）。
"""

from __future__ import annotations

import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from haven.storage.models import AuditLog

RULE_VERSION = "0.0.1"


def subject_key_for(user_id: str) -> str:
    """去标识主体键：sha256(user_id) 前 16 位，绝不落原始标识。"""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


async def record_audit(
    session: AsyncSession,
    subject_key: str | None,
    event_type: str,
    event_summary: str,
    rule_version: str = RULE_VERSION,
) -> None:
    session.add(
        AuditLog(
            subject_key=subject_key,
            event_type=event_type,
            event_summary=event_summary,
            rule_version=rule_version,
        )
    )
