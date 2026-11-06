"""ORM 表 —— 依 `src/haven/domain/models.py` 的 0.0.1 数据形状重写。

与 src 的差异：
- 无 users 表：**单租户部署（一个部署 = 一个人，见 ADR-006）**，`user_id` 存
  `HAVEN_OWNER_ID` 配置的本人 id。全部查询仍带 `user_id == uid` 过滤 ——
  它从"多用户隔离"退化为"单一命名空间"，机制不变，将来加人无需改表。
- `nickname`（称呼）落在 `HealthProfile`/`OnboardingDraft` 上，而非 REQ-005 D1 的
  User 表（该表刻意未实现）。它是档案数据，不是身份。
- 新增 `bp_pending_confirmations`（异常血压确定性二次确认的瞬时状态表）。
- 新增 `onboarding_drafts`（建档进度草稿，B3「可续接」的跨会话状态）。
- 审计表只存去标识 `subject_key` 与事件摘要，绝不含健康数值或对话原文。
- 时间统一存带时区的 UTC（Python 侧默认值），SQLite/PostgreSQL 通用。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from haven.storage.database import Base

PENDING_CONFIRM_TTL = timedelta(hours=24)

#: 建档草稿参与记忆注入的窗口 —— 草稿本身不设 TTL（用户回来时进度应当还在），
#: 但太久没动的草稿不再注入，避免模型对几个月前的半截进度"接着问"。
DRAFT_INJECTION_WINDOW = timedelta(days=7)


class HealthProfile(Base):
    __tablename__ = "health_profiles"

    profile_id: Mapped[object] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    #: 称呼（显示名称）—— 列名沿用 REQ-005 D1 的 nickname。单租户下 D1 User 表
    #: 刻意未实现，这个字段就落在唯一的"个人"表上；它是**档案数据不是身份**
    #: （身份是 HAVEN_OWNER_ID，见 ADR-006）。可跳过。
    nickname: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    birth_date: Mapped[date] = mapped_column(Date, nullable=False)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DiseaseRecord(Base):
    __tablename__ = "disease_records"

    disease_id: Mapped[object] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    disease_name: Mapped[str] = mapped_column(String(100), nullable=False)
    diagnosed_date: Mapped[date] = mapped_column(Date, nullable=False)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Active")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class BloodPressure(Base):
    __tablename__ = "blood_pressure_records"

    record_id: Mapped[object] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    systolic: Mapped[int] = mapped_column(Integer, nullable=False)
    diastolic: Mapped[int] = mapped_column(Integer, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="Manual")
    is_abnormal: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    consent_id: Mapped[object] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(20), nullable=False)
    scope: Mapped[str] = mapped_column(String(200), nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class PendingBpConfirmation(Base):
    """异常血压待确认态：一次一行（user_id 主键）。

    由 `record_blood_pressure` 写入（此时不落血压记录），
    由 `confirm_abnormal_blood_pressure` 校验匹配后消费删除并正式入库。
    这是「LLM 无法绕过二次确认」的确定性闸门。
    """

    __tablename__ = "bp_pending_confirmations"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    systolic: Mapped[int] = mapped_column(Integer, nullable=False)
    diastolic: Mapped[int] = mapped_column(Integer, nullable=False)
    measured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    prompt_text: Mapped[str] = mapped_column(String(500), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class OnboardingDraft(Base):
    """建档草稿：逐字段收集中的进度（一次一行，user_id 主键）。

    由 `save_onboarding_draft` 在用户每提供一项后 upsert，`next_field` 是
    状态机游标（B1 / S8.4）；为 None 表示六项已收集齐、等待用户 /confirm。

    清理三处：`save_health_profile` 成功（同一事务内删）、`/cancel`、
    `delete_my_data`。读侧另有 `DRAFT_INJECTION_WINDOW` 限制注入窗口。
    """

    __tablename__ = "onboarding_drafts"

    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    nickname: Mapped[str | None] = mapped_column(String(20), nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    disease_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    diagnosed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_field: Mapped[str | None] = mapped_column(String(20), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class AuditLog(Base):
    """最小审计日志 —— 绝不存储健康数值或对话原文（NF3.7 去标识）。"""

    __tablename__ = "audit_logs"

    audit_id: Mapped[object] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    subject_key: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    event_summary: Mapped[str] = mapped_column(String(500), nullable=False)
    rule_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
