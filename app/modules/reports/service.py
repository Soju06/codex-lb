from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.utils.time import to_utc_naive, utcnow
from app.modules.reports.repository import DailyReportLogRow, ReportsRepository
from app.modules.reports.schemas import (
    AccountCostEntry,
    DailyReportRow,
    ModelCostEntry,
    ReportComparison,
    ReportComparisonPrevious,
    ReportsResponse,
    ReportSummary,
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
    ) -> ReportsResponse:
        timezone_info = _resolve_timezone(report_timezone)
        now = utcnow().replace(tzinfo=timezone.utc).astimezone(timezone_info)
        if end_date is None:
            end_date = now.date()
        if start_date is None:
            start_date = end_date - timedelta(days=6)

        start_at = _local_midnight_to_utc_naive(start_date, timezone_info)
        end_at = _local_midnight_to_utc_naive(end_date + timedelta(days=1), timezone_info)
        window_days = max((end_date - start_date).days + 1, 1)
        previous_end_date = start_date - timedelta(days=1)
        previous_start_date = previous_end_date - timedelta(days=window_days - 1)
        previous_start_at = _local_midnight_to_utc_naive(previous_start_date, timezone_info)
        previous_end_at = _local_midnight_to_utc_naive(previous_end_date + timedelta(days=1), timezone_info)

        summary = await self._repository.aggregate_summary(start_at, end_at, account_ids, model)
        previous_summary = await self._repository.aggregate_summary(
            previous_start_at,
            previous_end_at,
            account_ids,
            model,
        )
        earliest_activity_at = await self._repository.earliest_report_activity_at(account_ids, model)
        daily_logs = await self._repository.list_daily_report_logs(start_at, end_at, account_ids, model)
        daily = _bucket_daily_rows(daily_logs, timezone_info)
        by_model = await self._repository.aggregate_by_model(start_at, end_at, account_ids, model)
        by_account = await self._repository.aggregate_by_account(start_at, end_at, account_ids, model)

        day_count = max((end_at.date() - start_at.date()).days, 1)

        model_total = sum(m.cost_usd for m in by_model)
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


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo | timezone:
    if not timezone_name:
        return timezone.utc
    try:
        return ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError):
        return timezone.utc


def _local_midnight_to_utc_naive(value: date, timezone_info: ZoneInfo | timezone) -> datetime:
    return to_utc_naive(datetime.combine(value, datetime.min.time(), tzinfo=timezone_info))


def _bucket_daily_rows(
    daily_logs: list[DailyReportLogRow],
    timezone_info: ZoneInfo | timezone,
) -> list[DailyReportRow]:
    buckets: OrderedDict[str, DailyReportRow] = OrderedDict()
    bucket_accounts: dict[str, set[str]] = {}

    for log in daily_logs:
        local_date = log.requested_at.replace(tzinfo=timezone.utc).astimezone(timezone_info).date().isoformat()
        row = buckets.get(local_date)
        if row is None:
            row = DailyReportRow(
                date=local_date,
                requests=0,
                input_tokens=0,
                output_tokens=0,
                cached_input_tokens=0,
                cost_usd=0.0,
                active_accounts=0,
                error_count=0,
            )
            buckets[local_date] = row
            bucket_accounts[local_date] = set()

        row.requests += 1
        row.input_tokens += log.input_tokens
        row.output_tokens += log.output_tokens
        row.cached_input_tokens += log.cached_input_tokens
        row.cost_usd += log.cost_usd
        row.error_count += int(log.is_error)
        if log.account_id is not None:
            bucket_accounts[local_date].add(log.account_id)
            row.active_accounts = len(bucket_accounts[local_date])

    for row in buckets.values():
        row.cost_usd = round(row.cost_usd, 4)

    return list(buckets.values())
