from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.utils.time import to_utc_naive, utcnow
from app.modules.reports.repository import MAX_DAILY_REPORT_DAYS, DailyReportRangeTooLargeError, ReportsRepository
from app.modules.reports.schemas import (
    AccountCostEntry,
    DailyReportRow,
    ModelCostEntry,
    ReportComparison,
    ReportComparisonPrevious,
    ReportsResponse,
    ReportSummary,
    UserAgentCostEntry,
)


class ReportsService:
    def __init__(self, repository: ReportsRepository) -> None:
        self._repository = repository

    async def get_reports(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        report_timezone: str | None = None,
        account_ids: list[str] | None = None,
        model: str | None = None,
        useragent_group: str | None = None,
    ) -> ReportsResponse:
        timezone_info = _resolve_timezone(report_timezone)
        now = utcnow().replace(tzinfo=timezone.utc).astimezone(timezone_info)
        if end_date is None:
            end_date = now.date()
        if start_date is None:
            start_date = end_date - timedelta(days=6)
        window_days = (end_date - start_date).days + 1
        if window_days > MAX_DAILY_REPORT_DAYS:
            raise DailyReportRangeTooLargeError(f"report date range must be {MAX_DAILY_REPORT_DAYS} days or less")

        start_at = _local_midnight_to_utc_naive(start_date, timezone_info)
        end_at = _local_midnight_to_utc_naive(end_date + timedelta(days=1), timezone_info)
        window_days = max(window_days, 1)
        previous_end_date = start_date - timedelta(days=1)
        previous_start_date = previous_end_date - timedelta(days=window_days - 1)
        previous_start_at = _local_midnight_to_utc_naive(previous_start_date, timezone_info)
        previous_end_at = _local_midnight_to_utc_naive(previous_end_date + timedelta(days=1), timezone_info)

        summary = await self._repository.aggregate_summary(start_at, end_at, account_ids, model, useragent_group)
        previous_summary = await self._repository.aggregate_summary(
            previous_start_at,
            previous_end_at,
            account_ids,
            model,
            useragent_group,
        )
        earliest_activity_at = await self._repository.earliest_report_activity_at(account_ids, model, useragent_group)
        daily_rows = await self._repository.aggregate_daily_rows(
            start_date,
            end_date,
            timezone_info,
            account_ids,
            model,
            useragent_group,
        )
        daily = [
            DailyReportRow(
                date=row.date,
                requests=row.requests,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cached_input_tokens=row.cached_input_tokens,
                cost_usd=round(row.cost_usd, 4),
                active_accounts=row.active_accounts,
                error_count=row.error_count,
            )
            for row in daily_rows
        ]
        by_model = await self._repository.aggregate_by_model(start_at, end_at, account_ids, model, useragent_group)
        by_account = await self._repository.aggregate_by_account(start_at, end_at, account_ids, model, useragent_group)
        by_useragent = await self._repository.aggregate_by_useragent(
            start_at,
            end_at,
            account_ids,
            model,
            useragent_group,
        )

        day_count = max((end_at.date() - start_at.date()).days, 1)

        model_total = sum(m.cost_usd for m in by_model)
        useragent_total = sum(u.cost_usd for u in by_useragent)
        comparison = ReportComparison(
            can_compare=earliest_activity_at is not None and earliest_activity_at <= previous_start_at,
            previous=ReportComparisonPrevious(
                total_cost_usd=round(previous_summary.total_cost_usd, 4),
                total_tokens=previous_summary.total_input_tokens + previous_summary.total_output_tokens,
                total_requests=previous_summary.total_requests,
            ),
        )

        return ReportsResponse(
            summary=ReportSummary(
                total_cost_usd=round(summary.total_cost_usd, 4),
                total_input_tokens=summary.total_input_tokens,
                total_output_tokens=summary.total_output_tokens,
                total_cached_tokens=summary.total_cached_tokens,
                total_requests=summary.total_requests,
                total_errors=summary.total_errors,
                active_accounts=summary.active_accounts,
                avg_cost_per_day=round(summary.total_cost_usd / day_count, 4),
                avg_requests_per_day=round(summary.total_requests / day_count, 2),
            ),
            comparison=comparison,
            daily=daily,
            by_model=[
                ModelCostEntry(
                    model=m.model,
                    cost_usd=round(m.cost_usd, 4),
                    requests=m.request_count,
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
            by_useragent=[
                UserAgentCostEntry(
                    useragent=u.useragent_group,
                    cost_usd=round(u.cost_usd, 4),
                    requests=u.request_count,
                    percentage=round((u.cost_usd / useragent_total * 100), 1) if useragent_total > 0 else 0,
                )
                for u in by_useragent
            ],
        )


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo | timezone:
    if not timezone_name:
        return timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return timezone.utc


def _local_midnight_to_utc_naive(value: date, timezone_info: ZoneInfo | timezone) -> datetime:
    return to_utc_naive(datetime.combine(value, datetime.min.time(), tzinfo=timezone_info))
