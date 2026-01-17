from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class UsageWindowRow:
    account_id: str
    used_percent: float | None
    reset_at: int | None = None
    window_minutes: int | None = None


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
