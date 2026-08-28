from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DeploymentMethod = Literal["docker", "k8s", "pip", "bare"]
ActiveConsentState = Literal["undecided", "enabled"]


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeploymentSnapshot(TelemetryModel):
    method: DeploymentMethod
    db_backend: Literal["sqlite", "postgres"]
    db_size_bucket: Literal["unknown", "<100MB", "100MB-1GB", "1-5GB", "5-10GB", "10-50GB", "50GB+"]
    replicas: int = Field(ge=1)
    reverse_proxy: bool


class PlanMixSnapshot(TelemetryModel):
    plus: str
    pro: str
    team: str
    free: str


class AccountsSnapshot(TelemetryModel):
    pool_bucket: str
    plan_mix: PlanMixSnapshot
    workspace_accounts: bool
    routing_policy: str
    limit_warmup_enabled: bool
    egress_proxy_used: bool


class RequestKindsSnapshot(TelemetryModel):
    responses: float
    chat: float
    images: float
    unknown: float


class TransportMixSnapshot(TelemetryModel):
    ws: float
    http_bridge: float


class ServiceTierMixSnapshot(TelemetryModel):
    default: float
    flex: float
    priority: float


class ModelUsageSnapshot(TelemetryModel):
    name: str
    share: float
    reasoning: dict[str, float]
    avg_output_tokens_bucket: str


class UsageSnapshot(TelemetryModel):
    requests: int = Field(ge=0)
    success_rate: float = Field(ge=0.0, le=1.0)
    tokens_input: int = Field(ge=0)
    tokens_output: int = Field(ge=0)
    tokens_cached_ratio: float = Field(ge=0.0, le=1.0)
    cost_usd_bucket: str
    request_kinds: RequestKindsSnapshot
    transport_mix: TransportMixSnapshot
    service_tier_mix: ServiceTierMixSnapshot
    clients: dict[str, float]
    clients_other_ratio: float = Field(ge=0.0, le=1.0)
    models: list[ModelUsageSnapshot]
    latency_ms_p50: int = Field(ge=0)
    ttft_ms_p50: int = Field(ge=0)
    ttft_ms_p95: int = Field(ge=0)
    rate_limit_429_ratio: float = Field(ge=0.0, le=1.0)
    top_upstream_errors: list[str] = Field(max_length=5)


class FeaturesSnapshot(TelemetryModel):
    api_firewall: bool
    quota_planner: bool
    sticky_sessions: bool
    conversation_archive: bool
    automations: bool
    fleet: bool
    model_sources_count: int = Field(ge=0)
    api_keys_bucket: str
    prometheus: bool
    otel: bool
    dashboard_auth: bool
    reset_credits: bool
    image_api_used: bool


class TelemetrySnapshot(TelemetryModel):
    schema_version: Literal[1] = 1
    consent: ActiveConsentState
    instance_id: str
    version: str
    python: str
    os: str
    arch: str
    uptime_hours: int = Field(ge=0)
    deploy: DeploymentSnapshot
    accounts: AccountsSnapshot
    usage_7d: UsageSnapshot
    features: FeaturesSnapshot


class TelemetryRegistration(TelemetryModel):
    app_name: Literal["codex-lb"] = "codex-lb"
    app_version: str
    deployment_mode: DeploymentMethod
    environment: str = ""
    instance_id: str
    os_arch: str
    public_key: str


class TelemetryActivation(TelemetryModel):
    action: Literal["activate"] = "activate"


class TelemetryOptOut(TelemetryModel):
    app_version: str
    event: Literal["optout"] = "optout"
    instance_id: str
    occurred_at: str


class TelemetrySnapshotEnvelope(TelemetryModel):
    instance_id: str
    metrics: TelemetrySnapshot
    timestamp: datetime


def build_snapshot_envelope(
    snapshot: TelemetrySnapshot,
    *,
    timestamp: datetime | None = None,
) -> TelemetrySnapshotEnvelope:
    return TelemetrySnapshotEnvelope(
        instance_id=snapshot.instance_id,
        metrics=snapshot,
        timestamp=timestamp or datetime.now(UTC),
    )


class TelemetryConsentUpdate(TelemetryModel):
    enabled: bool


class TelemetryConsentResponse(TelemetryModel):
    state: Literal["undecided", "enabled", "disabled"]
    source: Literal["env", "persisted", "default"]
    active: bool
    preview: TelemetrySnapshotEnvelope | None
