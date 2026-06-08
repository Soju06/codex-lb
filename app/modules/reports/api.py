from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.dependencies import ReportsContext, get_reports_context
from app.modules.reports.schemas import ReportsResponse

router = APIRouter(
    prefix="/api/reports",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=ReportsResponse)
async def get_reports(
    context: ReportsContext = Depends(get_reports_context),
    start_date: Annotated[date | None, Query()] = None,
    end_date: Annotated[date | None, Query()] = None,
    account_id: Annotated[list[str] | None, Query()] = None,
    model: Annotated[str | None, Query()] = None,
) -> ReportsResponse:
    start = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc) if start_date else None
    end = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc) if end_date else None
    return await context.service.get_reports(
        start_date=start,
        end_date=end,
        account_ids=account_id,
        model=model,
    )
