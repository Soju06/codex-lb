from __future__ import annotations

from datetime import datetime, timezone

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class DailyReportRow(DashboardModel):
    date: str
    requests: int
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int | None
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int
    conversations: int = 0
    error_count: int = 0
    cancelled_count: int = 0
    median_ttft_ms: float = 0.0
    median_tps: float = 0.0
    median_queue_ms: float = 0.0


class ModelCostEntry(DashboardModel):
    model: str
    cost_usd: float
    requests: int = 0
    percentage: float = 0.0


class AccountCostEntry(DashboardModel):
    account_id: str | None
    alias: str | None = None
    cost_usd: float = 0.0
    requests: int = 0


class UserAgentCostEntry(DashboardModel):
    useragent: str
    cost_usd: float = 0.0
    requests: int = 0
    percentage: float = 0.0


class ReportSummary(DashboardModel):
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    reasoning_usage_known_requests: int
    total_cached_tokens: int
    total_requests: int
    total_errors: int
    total_cancelled: int = 0
    active_accounts: int
    total_conversations: int = 0
    avg_cost_per_day: float = 0.0
    avg_requests_per_day: float = 0.0


class ReportComparisonPrevious(DashboardModel):
    total_cost_usd: float
    total_tokens: int
    total_requests: int


class ReportComparison(DashboardModel):
    can_compare: bool
    previous: ReportComparisonPrevious


class ReportsOptionsResponse(DashboardModel):
    models: list[str]
    useragents: list[str]


class ReportsResponse(DashboardModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    speed_metrics_available: bool = True
    speed_metrics_max_days: int = 7
    summary: ReportSummary
    comparison: ReportComparison
    daily: list[DailyReportRow] = Field(default_factory=list)
    by_model: list[ModelCostEntry] = Field(default_factory=list)
    by_account: list[AccountCostEntry] = Field(default_factory=list)
    by_useragent: list[UserAgentCostEntry] = Field(default_factory=list)


class ThreadIdentityFacet(DashboardModel):
    """One side of the keyed/unkeyed split for the selected window."""

    requests: int = 0
    request_share: float = 0.0
    # Requests whose account was detached by an account deletion. A rising
    # share means this window's account-spread figures are decaying.
    unattributed_request_share: float = 0.0
    conversations: int = 0
    mean_accounts_per_conversation: float = 0.0
    single_account_conversation_share: float = 0.0
    turns: int = 0
    account_switch_rate: float = 0.0
    cache_hit_ratio: float = 0.0
    cache_sample_input_tokens: int = 0
    # Unkeyed traffic carries no thread identifier, so its conversation and
    # switch figures are reconstructed by API key. The UI must label them.
    thread_grouping_approximate: bool = False


class ThreadIdentityResponse(DashboardModel):
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    available: bool = True
    max_days: int
    window_days: int
    conversation_min_requests: int
    switch_max_gap_seconds: int
    cache_min_input_tokens: int
    total_requests: int = 0
    unkeyed_request_share: float = 0.0
    keyed: ThreadIdentityFacet = Field(default_factory=ThreadIdentityFacet)
    unkeyed: ThreadIdentityFacet = Field(default_factory=ThreadIdentityFacet)
