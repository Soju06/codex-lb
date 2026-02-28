from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UsageWindowRow:
    account_id: str
    used_percent: float | None
    reset_at: int | None = None
    window_minutes: int | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True)
class UsageAggregateRow:
    account_id: str
    used_percent_avg: float | None
    input_tokens_sum: int | None
    output_tokens_sum: int | None
    samples: int
    last_recorded_at: datetime | None
    reset_at_max: int | None
    window_minutes_max: int | None

    def to_window_row(self) -> UsageWindowRow:
        return UsageWindowRow(
            account_id=self.account_id,
            used_percent=self.used_percent_avg,
            reset_at=self.reset_at_max,
            window_minutes=self.window_minutes_max,
            recorded_at=self.last_recorded_at,
        )


@dataclass(frozen=True)
class UsageWindowSummary:
    used_percent: float | None
    capacity_credits: float
    used_credits: float
    reset_at: int | None
    window_minutes: int | None


@dataclass(frozen=True)
class UsageWindowSnapshot:
    used_percent: float
    capacity_credits: float
    used_credits: float
    reset_at: int | None
    window_minutes: int | None


@dataclass(frozen=True)
class UsageCostByModel:
    model: str
    usd: float


@dataclass(frozen=True)
class UsageCostSummary:
    currency: str
    total_usd_7d: float
    by_model: list[UsageCostByModel]


@dataclass(frozen=True)
class UsageMetricsSummary:
    requests_7d: int | None
    tokens_secondary_window: int | None
    cached_tokens_secondary_window: int | None = None
    error_rate_7d: float | None = None
    top_error: str | None = None
    store_requested_7d: int | None = None
    response_context_responses: int | None = None
    response_context_items: int | None = None


@dataclass(frozen=True)
class UsageSummaryPayload:
    primary_window: UsageWindowSnapshot
    secondary_window: UsageWindowSnapshot | None
    cost: UsageCostSummary
    metrics: UsageMetricsSummary | None = None


@dataclass(frozen=True)
class UsageHistoryEntry:
    account_id: str
    email: str
    used_percent_avg: float
    used_credits: float
    request_count: int
    cost_usd: float


@dataclass(frozen=True)
class UsageHistoryPayload:
    window_hours: int
    accounts: list[UsageHistoryEntry]


@dataclass(frozen=True)
class UsageTrendBucket:
    bucket_epoch: int
    account_id: str
    window: str
    avg_used_percent: float
    samples: int


@dataclass(frozen=True)
class BucketModelAggregate:
    bucket_epoch: int
    model: str
    request_count: int
    error_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    reasoning_tokens: int
    store_requested_count: int = 0
