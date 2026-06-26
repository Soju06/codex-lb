from __future__ import annotations

import uuid
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
    literal_column,
    text,
    true,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    return [str(member.value) for member in enum_cls]


class AccountStatus(str, Enum):
    ACTIVE = "active"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    PAUSED = "paused"
    REAUTH_REQUIRED = "reauth_required"
    DEACTIVATED = "deactivated"


class AccountRoutingPolicy(str, Enum):
    NORMAL = "normal"
    BURN_FIRST = "burn_first"
    PRESERVE = "preserve"


class StickySessionKind(str, Enum):
    CODEX_SESSION = "codex_session"
    STICKY_THREAD = "sticky_thread"
    PROMPT_CACHE = "prompt_cache"


class RequestKind(str, Enum):
    NORMAL = "normal"
    WARMUP = "warmup"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    chatgpt_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str] = mapped_column(String, nullable=False)
    alias: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    workspace_label: Mapped[str | None] = mapped_column(String, nullable=True)
    seat_type: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_type: Mapped[str] = mapped_column(String, nullable=False)
    routing_policy: Mapped[str] = mapped_column(
        String,
        default="normal",
        server_default=text("'normal'"),
        nullable=False,
    )

    access_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    refresh_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    id_token_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    last_refresh: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    status: Mapped[AccountStatus] = mapped_column(
        SqlEnum(
            AccountStatus,
            name="account_status",
            validate_strings=True,
            values_callable=_enum_values,
        ),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    deactivation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocked_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    limit_warmup_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    security_work_authorized: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )

    api_key_assignments: Mapped[list["ApiKeyAccountAssignment"]] = relationship(
        "ApiKeyAccountAssignment",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    request_logs: Mapped[list["RequestLog"]] = relationship(
        "RequestLog",
        back_populates="account",
    )
    limit_warmups: Mapped[list["AccountLimitWarmup"]] = relationship(
        "AccountLimitWarmup",
        back_populates="account",
        cascade="all, delete-orphan",
    )
    proxy_binding: Mapped["AccountProxyBinding | None"] = relationship(
        "AccountProxyBinding",
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )


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


class AdditionalUsageHistory(Base):
    __tablename__ = "additional_usage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    quota_key: Mapped[str] = mapped_column(String, nullable=False)
    limit_name: Mapped[str] = mapped_column(String, nullable=False)
    metered_feature: Mapped[str] = mapped_column(String, nullable=False)
    window: Mapped[str] = mapped_column(String, nullable=False)
    used_percent: Mapped[float] = mapped_column(Float, nullable=False)
    reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class RequestLog(Base):
    __tablename__ = "request_logs"
    __table_args__ = (Index("idx_logs_useragent_group", "useragent_group"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    api_key_id: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    request_id: Mapped[str] = mapped_column(String, nullable=False)
    request_kind: Mapped[str] = mapped_column(
        String,
        default=RequestKind.NORMAL.value,
        server_default=text("'normal'"),
        nullable=False,
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    plan_type: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    useragent: Mapped[str | None] = mapped_column(Text, nullable=True)
    useragent_group: Mapped[str | None] = mapped_column(String, nullable=True)
    transport: Mapped[str | None] = mapped_column(String, nullable=True)
    service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    requested_service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    actual_service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_phase: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure_exception_type: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upstream_error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    bridge_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_route_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_transport: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_pool_id: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_endpoint_id: Mapped[str | None] = mapped_column(String, nullable=True)
    upstream_proxy_fallback_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    upstream_proxy_fail_closed_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    account: Mapped[Account | None] = relationship(
        "Account",
        back_populates="request_logs",
    )


class ProxyEndpoint(Base):
    __tablename__ = "proxy_endpoints"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    scheme: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    password_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    pool_memberships: Mapped[list["ProxyPoolMember"]] = relationship(
        "ProxyPoolMember",
        back_populates="endpoint",
        cascade="all, delete-orphan",
    )


class ProxyPool(Base):
    __tablename__ = "proxy_pools"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    members: Mapped[list["ProxyPoolMember"]] = relationship(
        "ProxyPoolMember",
        back_populates="pool",
        cascade="all, delete-orphan",
    )
    account_bindings: Mapped[list["AccountProxyBinding"]] = relationship(
        "AccountProxyBinding",
        back_populates="pool",
    )


class ProxyPoolMember(Base):
    __tablename__ = "proxy_pool_members"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pool_id: Mapped[str] = mapped_column(String, ForeignKey("proxy_pools.id", ondelete="CASCADE"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("proxy_endpoints.id", ondelete="CASCADE"),
        nullable=False,
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    pool: Mapped[ProxyPool] = relationship("ProxyPool", back_populates="members")
    endpoint: Mapped[ProxyEndpoint] = relationship("ProxyEndpoint", back_populates="pool_memberships")

    __table_args__ = (
        UniqueConstraint("pool_id", "endpoint_id", name="uq_proxy_pool_members_pool_endpoint"),
        Index("idx_proxy_pool_members_pool_order", "pool_id", "is_active", "sort_order", "id"),
    )


class AccountProxyBinding(Base):
    __tablename__ = "account_proxy_bindings"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    pool_id: Mapped[str] = mapped_column(String, ForeignKey("proxy_pools.id", ondelete="RESTRICT"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship("Account", back_populates="proxy_binding")
    pool: Mapped[ProxyPool] = relationship("ProxyPool", back_populates="account_bindings")

    __table_args__ = (UniqueConstraint("account_id", name="uq_account_proxy_bindings_account"),)


class AccountLimitWarmup(Base):
    __tablename__ = "account_limit_warmups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    window: Mapped[str] = mapped_column(String, nullable=False)
    reset_at: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(
        "Account",
        back_populates="limit_warmups",
    )

    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "window",
            "reset_at",
            name="uq_account_limit_warmups_account_window_reset",
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_ip: Mapped[str | None] = mapped_column(String(50), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(100), nullable=True)


class SchedulerLeader(Base):
    __tablename__ = "scheduler_leader"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    leader_id: Mapped[str] = mapped_column(String(100), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class StickySession(Base):
    __tablename__ = "sticky_sessions"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[StickySessionKind] = mapped_column(
        SqlEnum(
            StickySessionKind,
            name="sticky_session_kind",
            validate_strings=True,
            values_callable=_enum_values,
        ),
        primary_key=True,
        default=StickySessionKind.STICKY_THREAD,
        server_default=text("'sticky_thread'"),
        nullable=False,
    )
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
    sticky_threads_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    upstream_stream_transport: Mapped[str] = mapped_column(
        String,
        default="default",
        server_default=text("'default'"),
        nullable=False,
    )
    http_downstream_transport_policy: Mapped[str] = mapped_column(
        String,
        default="smart",
        server_default=text("'smart'"),
        nullable=False,
    )
    prefer_earlier_reset_accounts: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    prefer_earlier_reset_window: Mapped[str] = mapped_column(
        String,
        default="secondary",
        server_default=text("'secondary'"),
        nullable=False,
    )
    routing_strategy: Mapped[str] = mapped_column(
        String,
        default="capacity_weighted",
        server_default=text("'capacity_weighted'"),
        nullable=False,
    )
    relative_availability_power: Mapped[float] = mapped_column(
        Float,
        default=2.0,
        server_default=text("2.0"),
        nullable=False,
    )
    relative_availability_top_k: Mapped[int] = mapped_column(
        Integer,
        default=5,
        server_default=text("5"),
        nullable=False,
    )
    single_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    openai_cache_affinity_max_age_seconds: Mapped[int] = mapped_column(
        Integer,
        default=1800,
        server_default=text("1800"),
        nullable=False,
    )
    dashboard_session_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=43200,
        server_default=text("43200"),
        nullable=False,
    )
    import_without_overwrite: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=true(),
        nullable=False,
    )
    totp_required_on_login: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    guest_access_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    guest_password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    bootstrap_token_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    bootstrap_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    api_key_auth_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_last_verified_step: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        server_default=text("3600"),
        nullable=False,
    )
    http_responses_session_bridge_gateway_safe_mode: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    upstream_proxy_routing_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    upstream_proxy_default_pool_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("proxy_pools.id", ondelete="SET NULL"),
        nullable=True,
    )
    sticky_reallocation_budget_threshold_pct: Mapped[float] = mapped_column(
        Float,
        default=95.0,
        server_default=text("95.0"),
        nullable=False,
    )
    sticky_reallocation_primary_budget_threshold_pct: Mapped[float] = mapped_column(
        Float,
        default=95.0,
        server_default=text("95.0"),
        nullable=False,
    )
    sticky_reallocation_secondary_budget_threshold_pct: Mapped[float] = mapped_column(
        Float,
        default=100.0,
        server_default=text("100.0"),
        nullable=False,
    )
    additional_quota_routing_policies_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default=text("'{}'"),
        nullable=False,
    )
    limit_warmup_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    limit_warmup_windows: Mapped[str] = mapped_column(
        String,
        default="both",
        server_default=text("'both'"),
        nullable=False,
    )
    limit_warmup_model: Mapped[str] = mapped_column(
        String,
        default="auto",
        server_default=text("'auto'"),
        nullable=False,
    )
    limit_warmup_prompt: Mapped[str] = mapped_column(
        Text,
        default="Say OK.",
        server_default=text("'Say OK.'"),
        nullable=False,
    )
    limit_warmup_cooldown_seconds: Mapped[int] = mapped_column(
        Integer,
        default=3600,
        server_default=text("3600"),
        nullable=False,
    )
    limit_warmup_min_available_percent: Mapped[float] = mapped_column(
        Float,
        default=100.0,
        server_default=text("100.0"),
    )
    weekly_pace_working_days: Mapped[str] = mapped_column(
        String,
        default="0,1,2,3,4,5,6",
        server_default=text("'0,1,2,3,4,5,6'"),
        nullable=False,
    )
    warmup_model: Mapped[str] = mapped_column(
        String,
        default="gpt-5.4-mini",
        server_default=text("'gpt-5.4-mini'"),
        nullable=False,
    )
    additional_quota_routing_policies_json: Mapped[str] = mapped_column(
        Text,
        default="{}",
        server_default=text("'{}'"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ApiFirewallAllowlist(Base):
    __tablename__ = "api_firewall_allowlist"

    ip_address: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String, nullable=False)
    allowed_models: Mapped[str | None] = mapped_column(Text, nullable=True)
    apply_to_codex_model: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    enforced_model: Mapped[str | None] = mapped_column(String, nullable=True)
    enforced_reasoning_effort: Mapped[str | None] = mapped_column(String, nullable=True)
    enforced_service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    traffic_class: Mapped[str] = mapped_column(
        String,
        default="foreground",
        server_default=text("'foreground'"),
        nullable=False,
    )
    transport_policy_override: Mapped[str | None] = mapped_column(String, nullable=True)
    account_assignment_scope_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
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
    account_assignments: Mapped[list["ApiKeyAccountAssignment"]] = relationship(
        "ApiKeyAccountAssignment",
        back_populates="api_key",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ApiKeyAccountAssignment(Base):
    __tablename__ = "api_key_accounts"

    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        primary_key=True,
    )
    account_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    api_key: Mapped["ApiKey"] = relationship("ApiKey", back_populates="account_assignments")
    account: Mapped["Account"] = relationship("Account", back_populates="api_key_assignments")


class LimitType(str, Enum):
    TOTAL_TOKENS = "total_tokens"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    COST_USD = "cost_usd"
    CREDITS = "credits"


class LimitWindow(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    FIVE_HOURS = "5h"
    SEVEN_DAYS = "7d"


class ApiKeyLimit(Base):
    __tablename__ = "api_key_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("api_keys.id", ondelete="CASCADE"),
        nullable=False,
    )
    limit_type: Mapped[LimitType] = mapped_column(
        SqlEnum(
            LimitType,
            name="limit_type",
            validate_strings=True,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    limit_window: Mapped[LimitWindow] = mapped_column(
        SqlEnum(
            LimitWindow,
            name="limit_window",
            validate_strings=True,
            values_callable=_enum_values,
        ),
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


class RateLimitAttempt(Base):
    __tablename__ = "rate_limit_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)


class QuotaPlannerSettings(Base):
    __tablename__ = "quota_planner_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    mode: Mapped[str] = mapped_column(String, default="shadow", server_default=text("'shadow'"), nullable=False)
    timezone: Mapped[str] = mapped_column(String, default="UTC", server_default=text("'UTC'"), nullable=False)
    working_days_json: Mapped[str] = mapped_column(
        Text,
        default="[0,1,2,3,4]",
        server_default=text("'[0,1,2,3,4]'"),
        nullable=False,
    )
    working_hours_start: Mapped[str] = mapped_column(
        String,
        default="09:00",
        server_default=text("'09:00'"),
        nullable=False,
    )
    working_hours_end: Mapped[str] = mapped_column(
        String,
        default="18:00",
        server_default=text("'18:00'"),
        nullable=False,
    )
    prewarm_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    prewarm_lead_minutes: Mapped[int] = mapped_column(Integer, default=300, server_default=text("300"), nullable=False)
    max_warmups_per_day: Mapped[int] = mapped_column(Integer, default=3, server_default=text("3"), nullable=False)
    max_warmup_credits_per_day: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        server_default=text("0.0"),
        nullable=False,
    )
    min_expected_gain: Mapped[float] = mapped_column(Float, default=1.0, server_default=text("1.0"), nullable=False)
    forecast_quantile: Mapped[str] = mapped_column(String, default="p75", server_default=text("'p75'"), nullable=False)
    allow_synthetic_traffic: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=false(),
        nullable=False,
    )
    warmup_model_preference: Mapped[str | None] = mapped_column(String, nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class QuotaPlannerDecision(Base):
    __tablename__ = "quota_planner_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)
    account_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default=text("0.0"), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    forecast_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state_before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    state_after_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="planned", server_default=text("'planned'"), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String, nullable=False, unique=True)


class QuotaWindowObservation(Base):
    __tablename__ = "quota_window_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String, ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    primary_remaining_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    primary_reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    secondary_remaining_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    secondary_reset_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    confidence: Mapped[str] = mapped_column(String, default="unknown", server_default=text("'unknown'"), nullable=False)


class CacheInvalidation(Base):
    __tablename__ = "cache_invalidation"

    namespace: Mapped[str] = mapped_column(String(50), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class BridgeRingMember(Base):
    __tablename__ = "bridge_ring_members"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    instance_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    last_heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=func.now())
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class HttpBridgeSessionState(str, Enum):
    ACTIVE = "active"
    DRAINING = "draining"
    CLOSED = "closed"


class HttpBridgeSessionRecord(Base):
    __tablename__ = "http_bridge_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_key_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    session_key_value: Mapped[str] = mapped_column(Text, nullable=False)
    session_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_instance_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    owner_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    state: Mapped[HttpBridgeSessionState] = mapped_column(
        SqlEnum(
            HttpBridgeSessionState,
            name="http_bridge_session_state",
            validate_strings=True,
            values_callable=_enum_values,
        ),
        default=HttpBridgeSessionState.ACTIVE,
        server_default=text("'active'"),
        nullable=False,
    )
    account_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    service_tier: Mapped[str | None] = mapped_column(String, nullable=True)
    latest_turn_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_input_item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latest_input_full_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    aliases: Mapped[list["HttpBridgeSessionAlias"]] = relationship(
        "HttpBridgeSessionAlias",
        back_populates="session",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "session_key_kind",
            "session_key_hash",
            "api_key_scope",
            name="uq_http_bridge_sessions_session_key",
        ),
    )


class HttpBridgeSessionAlias(Base):
    __tablename__ = "http_bridge_session_aliases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("http_bridge_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    alias_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    alias_value: Mapped[str] = mapped_column(Text, nullable=False)
    alias_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    api_key_scope: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
        server_default=func.now(),
        onupdate=func.now(),
    )

    session: Mapped[HttpBridgeSessionRecord] = relationship(
        "HttpBridgeSessionRecord",
        back_populates="aliases",
    )

    __table_args__ = (
        UniqueConstraint(
            "alias_kind",
            "alias_hash",
            "api_key_scope",
            name="uq_http_bridge_session_aliases_alias",
        ),
    )


_PRIMARY_WINDOW_INDEX_EXPR = func.coalesce(UsageHistory.window, literal_column("'primary'"))

Index("idx_usage_recorded_at", UsageHistory.recorded_at)
Index("idx_usage_account_time", UsageHistory.account_id, UsageHistory.recorded_at)
Index(
    "idx_usage_window_account_time",
    _PRIMARY_WINDOW_INDEX_EXPR,
    UsageHistory.account_id,
    UsageHistory.recorded_at,
)
Index(
    "idx_usage_window_account_latest",
    _PRIMARY_WINDOW_INDEX_EXPR,
    UsageHistory.account_id,
    UsageHistory.recorded_at.desc(),
    UsageHistory.id.desc(),
)
Index(
    "idx_usage_window_raw_account_latest",
    UsageHistory.window,
    UsageHistory.account_id,
    UsageHistory.recorded_at.desc(),
    UsageHistory.id.desc(),
)
Index("idx_accounts_email", Account.email)
Index("idx_api_keys_name", ApiKey.name)
Index("idx_logs_account_time", RequestLog.account_id, RequestLog.requested_at)
Index("idx_logs_api_key_time", RequestLog.api_key_id, RequestLog.requested_at.desc(), RequestLog.id.desc())
Index("idx_logs_api_key_time_account", RequestLog.api_key_id, RequestLog.requested_at.desc(), RequestLog.account_id)
Index("idx_logs_request_kind_time", RequestLog.request_kind, RequestLog.requested_at.desc(), RequestLog.id.desc())
Index("idx_logs_requested_at", RequestLog.requested_at)
Index("idx_logs_source_requested_at", RequestLog.source, RequestLog.requested_at.desc())
Index("idx_logs_requested_at_id", RequestLog.requested_at.desc(), RequestLog.id.desc())
Index(
    "idx_logs_deleted_at_requested_at_id",
    RequestLog.deleted_at,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)
Index(
    "idx_logs_requested_at_model_tier",
    RequestLog.requested_at.desc(),
    RequestLog.model,
    RequestLog.service_tier,
)
Index(
    "idx_logs_model_effort_time",
    RequestLog.model,
    RequestLog.reasoning_effort,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)
Index(
    "idx_logs_status_error_time",
    RequestLog.status,
    RequestLog.error_code,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)
Index(
    "idx_logs_request_status_api_key_time",
    RequestLog.request_id,
    RequestLog.status,
    RequestLog.api_key_id,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)
Index(
    "idx_logs_request_status_api_key_session_time",
    RequestLog.request_id,
    RequestLog.status,
    RequestLog.api_key_id,
    RequestLog.session_id,
    RequestLog.requested_at.desc(),
    RequestLog.id.desc(),
)
Index("idx_sticky_account", StickySession.account_id)
Index("idx_sticky_kind_updated_at", StickySession.kind, StickySession.updated_at.desc())
Index("idx_api_keys_hash", ApiKey.key_hash)
Index(
    "idx_account_limit_warmups_account_attempted", AccountLimitWarmup.account_id, AccountLimitWarmup.attempted_at.desc()
)
Index("idx_account_limit_warmups_status_attempted", AccountLimitWarmup.status, AccountLimitWarmup.attempted_at.desc())
Index("idx_api_key_accounts_account_id", ApiKeyAccountAssignment.account_id)
Index("idx_api_key_limits_key_id", ApiKeyLimit.api_key_id)
Index("idx_api_key_limits_reset_at", ApiKeyLimit.reset_at)
Index("idx_api_key_usage_reservations_key_id", ApiKeyUsageReservation.api_key_id)
Index("idx_api_key_usage_reservations_status", ApiKeyUsageReservation.status)
Index(
    "idx_api_key_usage_reservations_status_updated_at", ApiKeyUsageReservation.status, ApiKeyUsageReservation.updated_at
)
Index("idx_api_key_usage_res_items_reservation_id", ApiKeyUsageReservationItem.reservation_id)
Index("idx_quota_planner_decisions_status_created", QuotaPlannerDecision.status, QuotaPlannerDecision.created_at.desc())
Index(
    "idx_quota_planner_decisions_account_created",
    QuotaPlannerDecision.account_id,
    QuotaPlannerDecision.created_at.desc(),
)
Index(
    "idx_quota_window_observations_account_time",
    QuotaWindowObservation.account_id,
    QuotaWindowObservation.observed_at.desc(),
)
Index("idx_http_bridge_sessions_owner_state", HttpBridgeSessionRecord.owner_instance_id, HttpBridgeSessionRecord.state)
Index("idx_http_bridge_sessions_lease", HttpBridgeSessionRecord.lease_expires_at)
Index("idx_http_bridge_sessions_last_seen", HttpBridgeSessionRecord.last_seen_at.desc())
Index(
    "idx_http_bridge_sessions_latest_turn_scope_state_seen",
    HttpBridgeSessionRecord.latest_turn_state,
    HttpBridgeSessionRecord.api_key_scope,
    HttpBridgeSessionRecord.state,
    HttpBridgeSessionRecord.last_seen_at.desc(),
    HttpBridgeSessionRecord.updated_at.desc(),
)
Index(
    "idx_http_bridge_sessions_latest_response_scope_state_seen",
    HttpBridgeSessionRecord.latest_response_id,
    HttpBridgeSessionRecord.api_key_scope,
    HttpBridgeSessionRecord.state,
    HttpBridgeSessionRecord.last_seen_at.desc(),
    HttpBridgeSessionRecord.updated_at.desc(),
)
Index(
    "idx_http_bridge_session_aliases_session_id",
    HttpBridgeSessionAlias.session_id,
)
Index(
    "idx_http_bridge_session_aliases_alias_kind_hash_scope",
    HttpBridgeSessionAlias.alias_kind,
    HttpBridgeSessionAlias.alias_hash,
    HttpBridgeSessionAlias.api_key_scope,
)
Index("ix_additional_usage_history_account_id", AdditionalUsageHistory.account_id)
Index("ix_additional_usage_history_recorded_at", AdditionalUsageHistory.recorded_at)
Index(
    "ix_rate_limit_attempts_type_key_attempted_at",
    RateLimitAttempt.type,
    RateLimitAttempt.key,
    RateLimitAttempt.attempted_at,
)
Index(
    "ix_additional_usage_history_composite",
    AdditionalUsageHistory.account_id,
    AdditionalUsageHistory.quota_key,
    AdditionalUsageHistory.window,
    AdditionalUsageHistory.recorded_at,
)
Index(
    "ix_additional_usage_quota_window",
    AdditionalUsageHistory.quota_key,
    AdditionalUsageHistory.window,
    AdditionalUsageHistory.account_id,
    AdditionalUsageHistory.recorded_at,
)
