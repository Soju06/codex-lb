from __future__ import annotations

from datetime import datetime, timedelta

from app.core.utils.time import to_utc_naive, utcnow
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.schemas import (
    AccountCostEntry,
    DailyReportRow,
    ModelCostEntry,
    ReportsResponse,
    ReportSummary,
)


class ReportsService:
    def __init__(self, repository: ReportsRepository) -> None:
        self._repository = repository

    async def get_reports(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        account_ids: list[str] | None = None,
        model: str | None = None,
    ) -> ReportsResponse:
        now = utcnow()
        if end_date is None:
            end_date = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if start_date is None:
            start_date = (end_date - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
        start_date = to_utc_naive(start_date)
        end_date = to_utc_naive(end_date)

        daily = await self._repository.aggregate_daily(start_date, end_date, account_ids, model)
        by_model = await self._repository.aggregate_by_model(start_date, end_date, account_ids, model)
        by_account = await self._repository.aggregate_by_account(start_date, end_date, account_ids, model)
        active_accounts = await self._repository.count_active_accounts(start_date, end_date, account_ids, model)

        total_cost = sum(d.cost_usd for d in daily)
        total_input = sum(d.input_tokens for d in daily)
        total_output = sum(d.output_tokens for d in daily)
        total_cached = sum(d.cached_input_tokens for d in daily)
        total_requests = sum(d.request_count for d in daily)
        total_errors = sum(d.error_count for d in daily)
        day_count = max((end_date.date() - start_date.date()).days, 1)

        model_total = sum(m.cost_usd for m in by_model)

        return ReportsResponse(
            summary=ReportSummary(
                total_cost_usd=round(total_cost, 4),
                total_input_tokens=total_input,
                total_output_tokens=total_output,
                total_cached_tokens=total_cached,
                total_requests=total_requests,
                total_errors=total_errors,
                active_accounts=active_accounts,
                avg_cost_per_day=round(total_cost / day_count, 4),
                avg_requests_per_day=round(total_requests / day_count, 2),
            ),
            daily=[
                DailyReportRow(
                    date=d.date,
                    requests=d.request_count,
                    input_tokens=d.input_tokens,
                    output_tokens=d.output_tokens,
                    cached_input_tokens=d.cached_input_tokens,
                    cost_usd=round(d.cost_usd, 4),
                    active_accounts=d.active_accounts,
                    error_count=d.error_count,
                )
                for d in daily
            ],
            by_model=[
                ModelCostEntry(
                    model=m.model,
                    cost_usd=round(m.cost_usd, 4),
                    percentage=round((m.cost_usd / model_total * 100), 1) if model_total > 0 else 0,
                )
                for m in by_model
            ],
            by_account=[
                AccountCostEntry(
                    account_id=a.account_id,
                    alias=a.alias,
                    cost_usd=round(a.cost_usd, 4),
                    requests=a.request_count,
                )
                for a in by_account
            ],
        )
