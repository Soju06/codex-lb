from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


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
    email: Mapped[str] = mapped_column(String, nullable=False)
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
    used_percent: Mapped[float] = mapped_column(Float, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_has: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credits_unlimited: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    credits_balance: Mapped[float | None] = mapped_column(Float, nullable=True)


class ModelOverride(Base):
    __tablename__ = "model_overrides"
    __table_args__ = (
        UniqueConstraint("match_type", "match_value", name="uq_model_overrides_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_type: Mapped[str] = mapped_column(String, nullable=False)
    match_value: Mapped[str] = mapped_column(String, nullable=False)
    forced_model: Mapped[str] = mapped_column(String, nullable=False)
    forced_reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("1"), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RequestLog(Base):
    __tablename__ = "request_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    api_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    requested_model: Mapped[str | None] = mapped_column(String, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String, nullable=True)
    client_app: Mapped[str | None] = mapped_column(String, nullable=True)
    auth_key_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    override_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("model_overrides.id", ondelete="SET NULL"),
        nullable=True,
    )
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
    routing_strategy: Mapped[str] = mapped_column(
        String,
        default="usage_weighted",
        server_default=text("'usage_weighted'"),
        nullable=False,
    )
    import_without_overwrite: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    totp_required_on_login: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_auth_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    global_model_force_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    global_model_force_model: Mapped[str | None] = mapped_column(String, nullable=True)
    global_model_force_reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_last_verified_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String, nullable=False)
    allowed_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    limits: Mapped[list["ApiKeyLimit"]] = relationship(
        "ApiKeyLimit",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class LimitType(str, Enum):
    TOTAL_TOKENS = "total_tokens"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST_USD = "cost_usd"


class LimitWindow(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ApiKeyLimit(Base):
    __tablename__ = "api_key_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_type: Mapped[LimitType] = mapped_column(
        SqlEnum(LimitType, name="limit_type", validate_strings=True),
        nullable=False,
    )
    limit_window: Mapped[LimitWindow] = mapped_column(
        SqlEnum(LimitWindow, name="limit_window", validate_strings=True),
        nullable=False,
    )
    max_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_value: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"), nullable=False)
    model_filter: Mapped[str | None] = mapped_column(String, nullable=True)
    reset_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    api_key: Mapped["ApiKey"] = relationship("ApiKey", back_populates="limits")


class ApiKeyUsageReservation(Base):
    __tablename__ = "api_key_usage_reservations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    model: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="reserved")
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cost_microdollars: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    items: Mapped[list["ApiKeyUsageReservationItem"]] = relationship(
        "ApiKeyUsageReservationItem",
        back_populates="reservation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ApiKeyUsageReservationItem(Base):
    __tablename__ = "api_key_usage_reservation_items"
    __table_args__ = (UniqueConstraint("reservation_id", "limit_id", name="uq_reservation_limit"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reservation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_key_usage_reservations.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("api_key_limits.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_type: Mapped[str] = mapped_column(String, nullable=False)
    reserved_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actual_delta: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_reset_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    reservation: Mapped[ApiKeyUsageReservation] = relationship(
        "ApiKeyUsageReservation",
        back_populates="items",
    )
    limit: Mapped[ApiKeyLimit] = relationship("ApiKeyLimit")


class ResponseContext(Base):
    __tablename__ = "response_context"

    response_id: Mapped[str] = mapped_column(String, primary_key=True)
    api_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    output_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResponseContextItem(Base):
    __tablename__ = "response_context_items"

    item_id: Mapped[str] = mapped_column(String, primary_key=True)
    response_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("response_context.response_id", ondelete="CASCADE"),
        nullable=False,
    )
    api_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    item_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


Index("idx_usage_recorded_at", UsageHistory.recorded_at)
Index("idx_usage_account_time", UsageHistory.account_id, UsageHistory.recorded_at)
Index("idx_accounts_email", Account.email)
Index("idx_logs_account_time", RequestLog.account_id, RequestLog.requested_at)
Index("idx_logs_requested_at", RequestLog.requested_at)
Index("idx_logs_requested_model", RequestLog.requested_model)
Index("idx_logs_client_ip", RequestLog.client_ip)
Index("idx_logs_client_app", RequestLog.client_app)
Index("idx_logs_auth_key_fingerprint", RequestLog.auth_key_fingerprint)
Index("idx_sticky_account", StickySession.account_id)
Index("idx_model_overrides_match_type_value", ModelOverride.match_type, ModelOverride.match_value)
Index("idx_api_keys_hash", ApiKey.key_hash)
Index("idx_api_key_limits_key_id", ApiKeyLimit.api_key_id)
Index("idx_api_key_usage_reservations_key_id", ApiKeyUsageReservation.api_key_id)
Index("idx_api_key_usage_reservations_status", ApiKeyUsageReservation.status)
Index("idx_api_key_usage_res_items_reservation_id", ApiKeyUsageReservationItem.reservation_id)
Index("idx_response_context_api_key", ResponseContext.api_key_id)
Index("idx_response_context_expires_at", ResponseContext.expires_at)
Index("idx_response_context_items_response_id", ResponseContextItem.response_id)
Index("idx_response_context_items_api_key", ResponseContextItem.api_key_id)
Index("idx_response_context_items_expires_at", ResponseContextItem.expires_at)
