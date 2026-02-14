from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AccountStatus(str, Enum):
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    PAUSED = "paused"
    DEACTIVATED = "deactivated"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    chatgpt_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    plan_type: Mapped[str] = mapped_column(String, nullable=False)

    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    last_refresh: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    status: Mapped[AccountStatus] = mapped_column(
        SqlEnum(AccountStatus, name="account_status", validate_strings=True),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    deactivation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)


class UsageHistory(Base):
    __tablename__ = "usage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    window: Mapped[str | None] = mapped_column(String, nullable=True)
    window_label: Mapped[str | None] = mapped_column(String, nullable=True)
    used_percent: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_has: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credits_unlimited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credits_balance: Mapped[float | None] = mapped_column(Float, nullable=True)


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class StickySession(Base):
    __tablename__ = "sticky_sessions"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class DashboardSettings(Base):
    __tablename__ = "dashboard_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    sticky_threads_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prefer_earlier_reset_accounts: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_required_on_login: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_last_verified_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


Index("idx_usage_recorded_at", UsageHistory.recorded_at)
Index("idx_usage_account_time", UsageHistory.account_id, UsageHistory.recorded_at)
Index("idx_logs_account_time", RequestLog.account_id, RequestLog.requested_at)
Index("idx_sticky_account", StickySession.account_id)
