from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth.dependencies import (
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError
from app.dependencies import ReportsContext, get_reports_caches, get_reports_context
from app.modules.reports.cache import ReportCacheKey, ReportsCaches
from app.modules.reports.repository import DailyReportRangeTooLargeError
from app.modules.reports.schemas import ReportsOptionsResponse, ReportsResponse
from app.modules.reports.service import InvalidReportDateRangeError, resolve_report_range

router = APIRouter(
    prefix="/api/reports",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=ReportsResponse)
async def get_reports(
    context: ReportsContext = Depends(get_reports_context),
    caches: ReportsCaches = Depends(get_reports_caches),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    report_timezone: Annotated[str | None, Query(alias="timezone")] = None,
    account_id: Annotated[list[str] | None, Query()] = None,
    api_key_id: Annotated[list[str] | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
    useragent_group: Annotated[str | None, Query()] = None,
) -> ReportsResponse:
    try:
        key = _cache_key(start_date, end_date, report_timezone, account_id, api_key_id, model, useragent_group)
        return await caches.reports.get(
            key,
            lambda: context.service.get_reports(
                start_date=key.start,
                end_date=key.end,
                report_timezone=key.timezone,
                account_ids=list(key.accounts) or None,
                api_key_ids=list(key.api_keys) or None,
                model=key.model or None,
                useragent_group=key.useragent or None,
            ),
        )
    except InvalidReportDateRangeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_report_date_range") from exc
    except DailyReportRangeTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/options", response_model=ReportsOptionsResponse)
async def get_report_options(
    context: ReportsContext = Depends(get_reports_context),
    caches: ReportsCaches = Depends(get_reports_caches),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    report_timezone: Annotated[str | None, Query(alias="timezone")] = None,
    account_id: Annotated[list[str] | None, Query()] = None,
    api_key_id: Annotated[list[str] | None, Query()] = None,
) -> ReportsOptionsResponse:
    try:
        key = _cache_key(start_date, end_date, report_timezone, account_id, api_key_id)
        return await caches.options.get(
            key,
            lambda: context.service.get_options(
                start_date=key.start,
                end_date=key.end,
                report_timezone=key.timezone,
                account_ids=list(key.accounts) or None,
                api_key_ids=list(key.api_keys) or None,
            ),
        )
    except InvalidReportDateRangeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_report_date_range") from exc
    except DailyReportRangeTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _cache_key(
    start: date | None,
    end: date | None,
    timezone: str | None,
    accounts: list[str] | None,
    api_keys: list[str] | None,
    model: str | None = None,
    useragent: str | None = None,
) -> ReportCacheKey:
    start, end, tz = resolve_report_range(start, end, timezone)
    return ReportCacheKey(
        start,
        end,
        str(tz),
        tuple(sorted(set(accounts or []))),
        tuple(sorted(set(api_keys or []))),
        model or "",
        useragent or "",
    )
