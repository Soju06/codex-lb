from __future__ import annotations

from dataclasses import dataclass

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow
from app.db.models import Account
from app.modules.dashboard.schemas import (
    DashboardOverviewSummary,
    DashboardOverviewTimeframe,
    DashboardOverviewTimeframeKey,
    DashboardUsageCost,
    DashboardUsageMetrics,
)
from app.modules.usage.builders import (
    ActivityCostSummary,
    ActivityMetricsSummary,
    build_usage_window_summary_model,
)


@dataclass(frozen=True)
class DashboardOverviewTimeframeConfig:
    key: DashboardOverviewTimeframeKey
    window_minutes: int
    bucket_seconds: int
    bucket_count: int


_OVERVIEW_TIMEFRAME_CONFIGS: dict[DashboardOverviewTimeframeKey, DashboardOverviewTimeframeConfig] = {
    "1d": DashboardOverviewTimeframeConfig(
        key="1d",
        window_minutes=1440,
        bucket_seconds=3600,
        bucket_count=24,
    ),
    "7d": DashboardOverviewTimeframeConfig(
        key="7d",
        window_minutes=10080,
        bucket_seconds=21600,
        bucket_count=28,
    ),
    "30d": DashboardOverviewTimeframeConfig(
        key="30d",
        window_minutes=43200,
        bucket_seconds=86400,
        bucket_count=30,
    ),
}


def resolve_overview_timeframe(
    key: DashboardOverviewTimeframeKey,
) -> DashboardOverviewTimeframeConfig:
    return _OVERVIEW_TIMEFRAME_CONFIGS[key]


def build_overview_timeframe(
    config: DashboardOverviewTimeframeConfig,
) -> DashboardOverviewTimeframe:
    return DashboardOverviewTimeframe(
        key=config.key,
        windowMinutes=config.window_minutes,
        bucketSeconds=config.bucket_seconds,
        bucketCount=config.bucket_count,
    )


def build_dashboard_overview_summary(
    *,
    accounts: list[Account],
    primary_rows: list[UsageWindowRow],
    secondary_rows: list[UsageWindowRow],
    activity_cost: ActivityCostSummary,
    activity_metrics: ActivityMetricsSummary,
) -> DashboardOverviewSummary:
    account_map = {account.id: account for account in accounts}
    primary_summary = usage_core.summarize_usage_window(primary_rows, account_map, "primary")
    secondary_summary = usage_core.summarize_usage_window(secondary_rows, account_map, "secondary")

    primary_window = build_usage_window_summary_model(usage_core.normalize_usage_window(primary_summary))
    secondary_window = build_usage_window_summary_model(usage_core.normalize_usage_window(secondary_summary))

    return DashboardOverviewSummary(
        primary_window=primary_window,
        secondary_window=secondary_window,
        cost=DashboardUsageCost(
            currency=activity_cost.currency,
            totalUsd=activity_cost.total_usd,
        ),
        metrics=DashboardUsageMetrics.model_validate(
            {
                "requests": activity_metrics.requests,
                "tokens": activity_metrics.tokens,
                "cached_input_tokens": activity_metrics.cached_input_tokens,
                "error_rate": activity_metrics.error_rate,
                "error_count": activity_metrics.error_count,
                "top_error": activity_metrics.top_error,
            }
        ),
    )
