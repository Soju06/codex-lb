from __future__ import annotations

import importlib.metadata
import logging
import os
import platform
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Literal, get_args

from sqlalchemy import and_, case, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from app import __version__
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.balancer.logic import RoutingStrategy
from app.core.config.settings import Settings, get_settings
from app.core.openai.model_registry import get_model_registry
from app.core.usage.logs import NON_ERROR_STATUSES
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    ApiFirewallAllowlist,
    ApiKey,
    AutomationJob,
    ModelSource,
    ProxyEndpoint,
    QuotaPlannerSettings,
    RequestLog,
)
from app.db.sqlite_utils import sqlite_db_path_from_url
from app.modules.reports.repository import ReportsRepository, _report_conditions
from app.modules.settings.repository import SettingsRepository
from app.modules.telemetry.clients import ClientCount, catalog_model_name, client_shares
from app.modules.telemetry.schemas import (
    AccountsSnapshot,
    ActiveConsentState,
    DeploymentMethod,
    DeploymentSnapshot,
    FeaturesSnapshot,
    ModelUsageSnapshot,
    PlanMixSnapshot,
    RequestKindsSnapshot,
    ServiceTierMixSnapshot,
    TelemetrySnapshot,
    TransportMixSnapshot,
    UsageSnapshot,
)

_PROCESS_STARTED = time.monotonic()
_MIB = 1024**2
_GIB = 1024**3
_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max", "ultra"})
_ROUTING_POLICIES: frozenset[str] = frozenset(get_args(RoutingStrategy))
_SAFE_UPSTREAM_ERROR_CODES = frozenset(
    {
        "authentication_error",
        "billing_not_active",
        "context_length_exceeded",
        "insufficient_quota",
        "invalid_api_key",
        "invalid_request_error",
        "model_not_found",
        "rate_limit_exceeded",
        "server_error",
        "server_overloaded",
        "service_unavailable",
        "usage_limit_reached",
        "upstream_unavailable",
    }
)

logger = logging.getLogger(__name__)

type Predicate = ColumnElement[bool]
type NullableIntegerColumn = InstrumentedAttribute[int | None]


def count_bucket(value: int) -> str:
    if value <= 0:
        return "0"
    if value == 1:
        return "1"
    if value <= 5:
        return "2-5"
    if value <= 20:
        return "6-20"
    if value <= 100:
        return "21-100"
    return "100+"


def db_size_bucket(
    size_bytes: int | None,
) -> Literal["unknown", "<100MB", "100MB-1GB", "1-5GB", "5-10GB", "10-50GB", "50GB+"]:
    if size_bytes is None:
        return "unknown"
    if size_bytes < 100 * _MIB:
        return "<100MB"
    if size_bytes < _GIB:
        return "100MB-1GB"
    if size_bytes < 5 * _GIB:
        return "1-5GB"
    if size_bytes < 10 * _GIB:
        return "5-10GB"
    if size_bytes < 50 * _GIB:
        return "10-50GB"
    return "50GB+"


def cost_bucket(cost_usd: float) -> str:
    if cost_usd < 10:
        return "<10"
    if cost_usd < 100:
        return "10-100"
    if cost_usd < 1_000:
        return "100-1k"
    if cost_usd < 10_000:
        return "1k-10k"
    if cost_usd < 50_000:
        return "10k-50k"
    return "50k+"


def output_tokens_bucket(tokens: float) -> str:
    if tokens < 250:
        return "<250"
    if tokens < 1_000:
        return "250-1k"
    if tokens < 4_000:
        return "1k-4k"
    if tokens < 16_000:
        return "4k-16k"
    return "16k+"


def _ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(min(1.0, max(0.0, float(numerator) / float(denominator))), 6)


@dataclass(slots=True)
class _ModelAccumulator:
    requests: int = 0
    output_tokens: int = 0
    reasoning_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))


class TelemetrySnapshotBuilder:
    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    async def build(
        self,
        instance_id: str,
        *,
        consent: ActiveConsentState,
    ) -> TelemetrySnapshot:
        now = utcnow()
        start = now - timedelta(days=7)
        reports = ReportsRepository(self._session)
        summary = await reports.aggregate_summary(start, now)
        ua_rows = await reports.aggregate_by_useragent(start, now)
        clients, clients_other_ratio = client_shares(
            ClientCount(raw_group=row.useragent_group, requests=row.request_count) for row in ua_rows
        )
        conditions = _report_conditions(start, now, None, None, None)
        dashboard_settings = await SettingsRepository(self._session).get_or_create()

        account_count, workspace_accounts, plan_counts = await self._account_aggregates()
        database_size = await self._database_size_bytes()
        models = await self._model_usage(conditions, summary.total_requests)
        request_kinds = self._request_kind_mix(summary.total_requests)
        transport_mix = await self._transport_mix(conditions, summary.total_requests)
        service_tier_mix = await self._service_tier_mix(conditions, summary.total_requests)
        latency_p50 = await self._percentile(RequestLog.latency_ms, conditions, 0.50)
        ttft_p50 = await self._percentile(RequestLog.latency_first_token_ms, conditions, 0.50)
        ttft_p95 = await self._percentile(RequestLog.latency_first_token_ms, conditions, 0.95)
        rate_limit_429_count = await self._count_where(conditions, RequestLog.upstream_status_code == 429)
        top_errors = await self._top_upstream_errors(conditions)
        feature_counts = await self._feature_counts(conditions)

        method = deployment_method()
        db_backend = "postgres" if self._session.get_bind().dialect.name == "postgresql" else "sqlite"
        plan_mix = PlanMixSnapshot(
            plus=count_bucket(plan_counts.get("plus", 0)),
            pro=count_bucket(plan_counts.get("pro", 0)),
            team=count_bucket(plan_counts.get("team", 0)),
            free=count_bucket(plan_counts.get("free", 0)),
        )
        return TelemetrySnapshot(
            consent=consent,
            instance_id=instance_id,
            version=__version__,
            python=f"{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}",
            os=platform.system().lower(),
            arch=platform.machine().lower(),
            uptime_hours=max(0, int((time.monotonic() - _PROCESS_STARTED) // 3600)),
            deploy=DeploymentSnapshot(
                method=method,
                db_backend=db_backend,
                db_size_bucket=db_size_bucket(database_size),
                replicas=max(1, len(self._settings.http_responses_session_bridge_instance_ring)),
                reverse_proxy=self._settings.firewall_trust_proxy_headers,
            ),
            accounts=AccountsSnapshot(
                pool_bucket=count_bucket(account_count),
                plan_mix=plan_mix,
                workspace_accounts=workspace_accounts,
                routing_policy=_canonical_routing_policy(dashboard_settings.routing_strategy),
                limit_warmup_enabled=dashboard_settings.limit_warmup_enabled,
                egress_proxy_used=(
                    dashboard_settings.upstream_proxy_routing_enabled or feature_counts.active_proxy_endpoints > 0
                ),
            ),
            usage_7d=UsageSnapshot(
                requests=summary.total_requests,
                # Cancelled terminals are neither errors nor successes
                # (NON_ERROR_STATUSES); counting them as successes would
                # inflate the rate on disconnect-heavy workloads.
                success_rate=_ratio(
                    summary.total_requests - summary.total_errors - summary.total_cancelled,
                    summary.total_requests,
                ),
                tokens_input=summary.total_input_tokens,
                tokens_output=summary.total_output_tokens,
                tokens_cached_ratio=_ratio(summary.total_cached_tokens, summary.total_input_tokens),
                cost_usd_bucket=cost_bucket(max(0.0, summary.total_cost_usd)),
                request_kinds=request_kinds,
                transport_mix=transport_mix,
                service_tier_mix=service_tier_mix,
                clients=clients,
                clients_other_ratio=clients_other_ratio,
                models=models,
                latency_ms_p50=latency_p50,
                ttft_ms_p50=ttft_p50,
                ttft_ms_p95=ttft_p95,
                rate_limit_429_ratio=_ratio(rate_limit_429_count, summary.total_requests),
                top_upstream_errors=top_errors,
            ),
            features=FeaturesSnapshot(
                api_firewall=feature_counts.firewall_entries > 0,
                quota_planner=(
                    self._settings.quota_planner_scheduler_enabled and feature_counts.quota_planner_mode != "off"
                ),
                sticky_sessions=dashboard_settings.sticky_threads_enabled,
                conversation_archive=self._settings.conversation_archive_enabled,
                automations=(self._settings.automations_scheduler_enabled and feature_counts.enabled_automations > 0),
                fleet=True,
                model_sources_count=feature_counts.model_sources,
                api_keys_bucket=count_bucket(feature_counts.api_keys),
                prometheus=self._settings.metrics_enabled,
                otel=self._settings.otel_enabled,
                dashboard_auth=self._settings.dashboard_auth_mode != DashboardAuthMode.DISABLED,
                reset_credits=(
                    dashboard_settings.show_reset_credit_badges
                    or dashboard_settings.auto_redeem_reset_credits_before_expiry
                ),
                image_api_used=feature_counts.image_requests > 0,
            ),
        )

    async def _account_aggregates(self) -> tuple[int, bool, dict[str, int]]:
        result = await self._session.execute(
            select(
                func.count().label("accounts"),
                func.coalesce(func.sum(case((Account.workspace_id.is_not(None), 1), else_=0)), 0).label("workspace"),
            )
        )
        row = result.one()
        plan_result = await self._session.execute(select(Account.plan_type, func.count()).group_by(Account.plan_type))
        plan_counts: defaultdict[str, int] = defaultdict(int)
        for raw_plan, raw_count in plan_result.all():
            plan_counts[_canonical_plan(raw_plan)] += int(raw_count)
        return int(row.accounts), bool(row.workspace), dict(plan_counts)

    async def _model_usage(self, conditions: list[Predicate], total_requests: int) -> list[ModelUsageSnapshot]:
        result = await self._session.execute(
            select(
                RequestLog.model,
                RequestLog.reasoning_effort,
                func.count().label("requests"),
                func.coalesce(func.sum(RequestLog.output_tokens), 0).label("output_tokens"),
            )
            .where(and_(*conditions))
            .group_by(RequestLog.model, RequestLog.reasoning_effort)
        )
        catalog = frozenset(get_model_registry().get_models_with_fallback())
        grouped: defaultdict[str, _ModelAccumulator] = defaultdict(_ModelAccumulator)
        for row in result.all():
            name = catalog_model_name(row.model, catalog)
            accumulator = grouped[name]
            count = int(row.requests)
            accumulator.requests += count
            accumulator.output_tokens += int(row.output_tokens)
            reasoning = _canonical_reasoning(row.reasoning_effort)
            accumulator.reasoning_counts[reasoning] += count
        return [
            ModelUsageSnapshot(
                name=name,
                share=_ratio(values.requests, total_requests),
                reasoning={
                    reasoning: _ratio(count, values.requests)
                    for reasoning, count in sorted(values.reasoning_counts.items())
                },
                avg_output_tokens_bucket=output_tokens_bucket(
                    values.output_tokens / values.requests if values.requests else 0
                ),
            )
            for name, values in sorted(grouped.items())
        ]

    def _request_kind_mix(self, total: int) -> RequestKindsSnapshot:
        # ``request_logs.request_kind`` records workload classes such as
        # normal/warmup/compaction, not the ingress route family. Chat,
        # Responses, images, and audio can therefore be indistinguishable in
        # persisted rows. Report that limitation instead of inferring a route
        # from the upstream source or model name.
        return RequestKindsSnapshot(
            responses=0.0,
            chat=0.0,
            images=0.0,
            unknown=1.0 if total else 0.0,
        )

    async def _transport_mix(self, conditions: list[Predicate], total: int) -> TransportMixSnapshot:
        websocket = await self._count_where(conditions, RequestLog.transport == "websocket")
        return TransportMixSnapshot(ws=_ratio(websocket, total), http_bridge=_ratio(total - websocket, total))

    async def _service_tier_mix(self, conditions: list[Predicate], total: int) -> ServiceTierMixSnapshot:
        # "fast" is normalized to "priority" at write time
        # (_normalize_service_tier_value), so the persisted vocabulary here is
        # default/flex/priority; lumping priority into default would hide fast
        # mode traffic from the mix.
        tier = func.coalesce(RequestLog.actual_service_tier, RequestLog.service_tier, "default")
        flex = await self._count_where(conditions, tier == "flex")
        priority = await self._count_where(conditions, tier == "priority")
        return ServiceTierMixSnapshot(
            default=_ratio(total - flex - priority, total),
            flex=_ratio(flex, total),
            priority=_ratio(priority, total),
        )

    async def _percentile(
        self,
        column: NullableIntegerColumn,
        conditions: list[Predicate],
        quantile: float,
    ) -> int:
        count_result = await self._session.execute(
            select(func.count()).where(and_(*conditions, column.is_not(None), column >= 0))
        )
        count = int(count_result.scalar_one())
        if count == 0:
            return 0
        rank = min(count - 1, max(0, int((count - 1) * quantile + 0.5)))
        result = await self._session.execute(
            select(column)
            .where(and_(*conditions, column.is_not(None), column >= 0))
            .order_by(column)
            .offset(rank)
            .limit(1)
        )
        value = result.scalar_one()
        if value is None:
            raise RuntimeError("percentile query returned a null value after a non-null filter")
        return int(value)

    async def _count_where(self, conditions: list[Predicate], *extra_conditions: Predicate) -> int:
        result = await self._session.execute(select(func.count()).where(and_(*conditions, *extra_conditions)))
        return int(result.scalar_one())

    async def _top_upstream_errors(self, conditions: list[Predicate]) -> list[str]:
        # Cancelled rows keep upstream_error_code='client_disconnected', so
        # filtering on the code alone would let routine disconnects displace
        # genuine upstream failures; restrict to actual error statuses like
        # the other error-metric surfaces.
        result = await self._session.execute(
            select(RequestLog.upstream_error_code, func.count().label("requests"))
            .where(
                and_(
                    *conditions,
                    RequestLog.upstream_error_code.is_not(None),
                    RequestLog.status.not_in(NON_ERROR_STATUSES),
                )
            )
            .group_by(RequestLog.upstream_error_code)
        )
        counts: defaultdict[str, int] = defaultdict(int)
        for raw_code, count in result.all():
            code = raw_code if raw_code in _SAFE_UPSTREAM_ERROR_CODES else "other"
            counts[code] += int(count)
        return [code for code, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]]

    async def _feature_counts(self, conditions: list[Predicate]) -> _FeatureCounts:
        scalar_queries = (
            select(func.count()).select_from(ApiFirewallAllowlist),
            select(func.count()).select_from(ApiKey),
            select(func.count()).select_from(ModelSource).where(ModelSource.is_enabled.is_(True)),
            select(func.count()).select_from(ProxyEndpoint).where(ProxyEndpoint.is_active.is_(True)),
            select(func.count()).select_from(AutomationJob).where(AutomationJob.enabled.is_(True)),
            select(func.count()).select_from(RequestLog).where(and_(*conditions, RequestLog.model.like("gpt-image-%"))),
            select(QuotaPlannerSettings.mode).where(QuotaPlannerSettings.id == 1),
        )
        values = []
        for query in scalar_queries:
            values.append((await self._session.execute(query)).scalar_one_or_none())
        return _FeatureCounts(
            firewall_entries=int(values[0] or 0),
            api_keys=int(values[1] or 0),
            model_sources=int(values[2] or 0),
            active_proxy_endpoints=int(values[3] or 0),
            enabled_automations=int(values[4] or 0),
            image_requests=int(values[5] or 0),
            quota_planner_mode=str(values[6] or "shadow"),
        )

    async def _database_size_bytes(self) -> int | None:
        if self._session.get_bind().dialect.name == "postgresql":
            result = await self._session.execute(text("SELECT pg_database_size(current_database())"))
            return int(result.scalar_one())
        path = sqlite_db_path_from_url(self._settings.database_url)
        if path is None:
            return None
        try:
            return Path(path).stat().st_size
        except OSError as exc:
            logger.debug("Unable to measure SQLite database size path=%s", path, exc_info=exc)
            return None


@dataclass(frozen=True, slots=True)
class _FeatureCounts:
    firewall_entries: int
    api_keys: int
    model_sources: int
    active_proxy_endpoints: int
    enabled_automations: int
    image_requests: int
    quota_planner_mode: str


def _canonical_plan(raw_plan: str | None) -> str:
    normalized = (raw_plan or "").strip().lower()
    if normalized in {"pro", "prolite"}:
        return "pro"
    if normalized in {"team", "business", "enterprise", "edu", "education"}:
        return "team"
    if normalized == "plus":
        return "plus"
    return "free"


def _canonical_reasoning(raw_effort: str | None) -> str:
    normalized = (raw_effort or "").strip().lower()
    if not normalized:
        return "unspecified"
    return normalized if normalized in _REASONING_EFFORTS else "other"


def _canonical_routing_policy(raw_policy: str | None) -> str:
    normalized = (raw_policy or "").strip().lower()
    return normalized if normalized in _ROUTING_POLICIES else "other"


def deployment_method() -> DeploymentMethod:
    if os.environ.get("KUBERNETES_SERVICE_HOST") or Path("/var/run/secrets/kubernetes.io/serviceaccount").exists():
        return "k8s"
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return "docker"
    try:
        importlib.metadata.distribution("codex-lb")
    except importlib.metadata.PackageNotFoundError:
        return "bare"
    return "pip"
