from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DeploymentMethod = Literal["docker", "k8s", "pip", "bare"]
ActiveConsentState = Literal["undecided", "enabled"]
LATENCY_BUCKET_EDGES: tuple[float, ...] = (
    0,
    50,
    100,
    200,
    400,
    800,
    1600,
    3200,
    6400,
    12800,
    25600,
    51200,
    102400,
    float("inf"),
)
TTFT_BUCKET_EDGES = LATENCY_BUCKET_EDGES
TPS_BUCKET_EDGES: tuple[float, ...] = (0, 5, 10, 20, 40, 80, 160, 320, 640, 1280, float("inf"))


class TelemetryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Histogram(TelemetryModel):
    sample_count: int = Field(ge=0)
    buckets: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bucket_sum_matches(self) -> Histogram:
        if any((not key.isdigit() or int(key) < 0 or value < 0) for key, value in self.buckets.items()):
            raise ValueError("histogram bucket indexes and counts must be non-negative")
        if self.sample_count != sum(self.buckets.values()):
            raise ValueError("histogram sample_count must equal bucket sum")
        return self


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
    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_shape(cls, value):
        if isinstance(value, dict) and "pool_bucket" in value:
            value = {
                **value,
                "total": 0 if value["pool_bucket"] == "0" else 1,
                "per_plan": {key: 0 for key in ("plus", "pro", "team", "free")},
                "per_status": {},
            }
            value.pop("pool_bucket", None)
            value.pop("plan_mix", None)
        return value

    total: int = Field(ge=0)
    per_plan: dict[str, int]
    per_status: dict[str, int]
    workspace_accounts: bool
    routing_policy: str
    limit_warmup_enabled: bool
    egress_proxy_used: bool


class RequestKindsSnapshot(TelemetryModel):
    responses: int = Field(ge=0)
    chat: int = Field(ge=0)
    images: int = Field(ge=0)
    unknown: int = Field(ge=0)


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
    schema_version: Literal[2] = 2
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


class DimensionEntry(TelemetryModel):
    name: str
    requests: int = Field(ge=0)
    latency_ms: Histogram
    ttft_ms: Histogram
    tps: Histogram


class DayDimensions(TelemetryModel):
    models: list[DimensionEntry]
    clients: list[DimensionEntry]
    transport: list[DimensionEntry]
    upstream_transport: list[DimensionEntry]
    service_tier: list[DimensionEntry]
    request_kinds: RequestKindsSnapshot
    global_: DimensionEntry = Field(alias="global")
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Outcomes(TelemetryModel):
    success: int = Field(ge=0)
    error: int = Field(ge=0)
    cancelled: int = Field(ge=0)


class DayErrors(TelemetryModel):
    upstream_error_class: dict[str, int]
    failure_phase: dict[str, int]
    http_status_class: dict[str, int]
    outcomes: Outcomes


class TelemetryDay(TelemetryModel):
    schema_version: Literal[2] = 2
    instance_id: str
    utc_date: date
    dimensions: DayDimensions
    errors: DayErrors


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
    snapshot: TelemetrySnapshot, *, timestamp: datetime | None = None
) -> TelemetrySnapshotEnvelope:
    return TelemetrySnapshotEnvelope(
        instance_id=snapshot.instance_id, metrics=snapshot, timestamp=timestamp or datetime.now(UTC)
    )


class TelemetryPreview(TelemetryModel):
    heartbeat: TelemetrySnapshotEnvelope
    day: TelemetryDay


class TelemetryConsentUpdate(TelemetryModel):
    enabled: bool


class TelemetryConsentResponse(TelemetryModel):
    state: Literal["undecided", "enabled", "disabled"]
    source: Literal["env", "persisted", "default"]
    active: bool
    notice_version: int
    preview: TelemetryPreview | None


def merge_histograms(*histograms: Histogram) -> Histogram:
    """Merge fixed-edge histograms without interpolation."""
    counts: dict[str, int] = {}
    for histogram in histograms:
        for index, count in histogram.buckets.items():
            counts[index] = counts.get(index, 0) + count
    return Histogram(sample_count=sum(counts.values()), buckets={key: value for key, value in counts.items() if value})


def percentile_interval(histogram: Histogram, edges: tuple[float, ...], quantile: float) -> tuple[float, float]:
    if not 0 <= quantile <= 1 or histogram.sample_count == 0:
        raise ValueError("quantile requires a non-empty histogram and must be between zero and one")
    rank = max(1, int(histogram.sample_count * quantile + 0.999999))
    cumulative = 0
    for index, edge in enumerate(edges):
        cumulative += histogram.buckets.get(str(index), 0)
        if cumulative >= rank:
            lower = 0.0 if index == 0 else edges[index - 1]
            return lower, edge
    return edges[-2], edges[-1]
