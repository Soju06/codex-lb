from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import (
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError
from app.db.session import get_session
from app.modules.read_snapshots.service import SnapshotReader
from app.modules.reports.cache import ReportCacheKey
from app.modules.reports.repository import DailyReportRangeTooLargeError
from app.modules.reports.schemas import ReportsOptionsResponse, ReportsResponse, ThreadIdentityResponse
from app.modules.reports.service import InvalidReportDateRangeError, resolve_report_range

router = APIRouter(
    prefix="/api/reports",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=ReportsResponse)
async def get_reports(
    session: AsyncSession = Depends(get_session),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    report_timezone: Annotated[str | None, Query(alias="timezone")] = None,
    account_id: Annotated[list[str] | None, Query()] = None,
    api_key_id: Annotated[list[str] | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
    useragent_group: Annotated[str | None, Query()] = None,
) -> ReportsResponse:
    try:
        value = await SnapshotReader(session).report(
            "report",
            start=start_date,
            end=end_date,
            timezone=report_timezone,
            accounts=account_id,
            api_keys=api_key_id,
            model=model,
            useragent=useragent_group,
        )
        return ReportsResponse.model_validate(value)
    except InvalidReportDateRangeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_report_date_range") from exc
    except DailyReportRangeTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/thread-identity", response_model=ThreadIdentityResponse)
async def get_thread_identity(
    session: AsyncSession = Depends(get_session),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    report_timezone: Annotated[str | None, Query(alias="timezone")] = None,
) -> ThreadIdentityResponse:
    """Thread identity and cache locality over the selected range, pool-wide.

    The Reports filters are intentionally not accepted here: filtering by
    account would pin every conversation to one account and make the
    accounts-per-conversation factor meaningless.
    """
    try:
        value = await SnapshotReader(session).report(
            "threadIdentity",
            start=start_date,
            end=end_date,
            timezone=report_timezone,
        )
        return ThreadIdentityResponse.model_validate(value)
    except InvalidReportDateRangeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_report_date_range") from exc
    except DailyReportRangeTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/options", response_model=ReportsOptionsResponse)
async def get_report_options(
    session: AsyncSession = Depends(get_session),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    report_timezone: Annotated[str | None, Query(alias="timezone")] = None,
    account_id: Annotated[list[str] | None, Query()] = None,
    api_key_id: Annotated[list[str] | None, Query()] = None,
) -> ReportsOptionsResponse:
    try:
        value = await SnapshotReader(session).report(
            "options",
            start=start_date,
            end=end_date,
            timezone=report_timezone,
            accounts=account_id,
            api_keys=api_key_id,
        )
        return ReportsOptionsResponse.model_validate(value)
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
