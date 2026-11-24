"""Background-only collectors. HTTP handlers import the reader, not this module."""

from __future__ import annotations

from datetime import date
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonValue
from app.modules.dashboard.repository import DashboardRepository
from app.modules.dashboard.service import DashboardService
from app.modules.read_snapshots.keys import DASHBOARD_RANGES, resolve_parameters
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.service import ReportsService


async def dashboard_snapshot(session: AsyncSession) -> dict[str, JsonValue]:
    service = DashboardService(DashboardRepository(session))
    projections = await service.get_projections()
    overviews: dict[str, JsonValue] = {}
    for timeframe in DASHBOARD_RANGES:
        overview = await service.get_overview(timeframe, precomputed_projections=projections)
        overviews[timeframe] = overview.model_dump(mode="json", by_alias=True)
    return {
        "overviews": overviews,
        "projections": projections.model_dump(mode="json", by_alias=True),
    }


async def report_snapshot(session: AsyncSession, parameters: dict[str, JsonValue]) -> dict[str, JsonValue]:
    values = resolve_parameters(parameters)
    start = date.fromisoformat(str(values["start"]))
    end = date.fromisoformat(str(values["end"]))
    zone = str(values["timezone"])
    accounts = cast(list[str], values.get("accounts", [])) or None
    api_keys = cast(list[str], values.get("apiKeys", [])) or None
    model = str(values.get("model") or "") or None
    useragent = str(values.get("useragent") or "") or None
    service = ReportsService(ReportsRepository(session))
    report = await service.get_reports(start, end, zone, accounts, model, useragent, api_keys)
    options = await service.get_options(start, end, zone, accounts, api_keys)
    result: dict[str, JsonValue] = {
        "range": {"start": start.isoformat(), "end": end.isoformat(), "timezone": zone},
        "report": report.model_dump(mode="json", by_alias=True),
        "options": options.model_dump(mode="json", by_alias=True),
    }
    # Thread identity is intentionally pool-wide, never recomputed per filter.
    if not any((accounts, api_keys, model, useragent)):
        identity = await service.get_thread_identity(start, end, zone)
        result["threadIdentity"] = identity.model_dump(mode="json", by_alias=True)
    return result
