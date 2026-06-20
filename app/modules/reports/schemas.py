from __future__ import annotations

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class DailyReportRow(DashboardModel):
    date: str
    requests: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    cost_usd: float
    active_accounts: int
    error_count: int = 0


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
    total_cached_tokens: int
    total_requests: int
    total_errors: int
    active_accounts: int
    avg_cost_per_day: float = 0.0
    avg_requests_per_day: float = 0.0


class ReportComparisonPrevious(DashboardModel):
    total_cost_usd: float
    total_tokens: int
    total_requests: int


class ReportComparison(DashboardModel):
    can_compare: bool
    previous: ReportComparisonPrevious


class ReportsResponse(DashboardModel):
    summary: ReportSummary
    comparison: ReportComparison
    daily: list[DailyReportRow] = Field(default_factory=list)
    by_model: list[ModelCostEntry] = Field(default_factory=list)
    by_account: list[AccountCostEntry] = Field(default_factory=list)
    by_useragent: list[UserAgentCostEntry] = Field(default_factory=list)
