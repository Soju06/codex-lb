"""Request-side snapshot selection. No analytical service is invoked here."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DashboardBadRequestError, DashboardServiceUnavailableError
from app.core.types import JsonValue
from app.core.utils.masking import mask_email
from app.modules.dashboard.schemas import DashboardOverviewTimeframeKey
from app.modules.read_snapshots.keys import report_parameters, resolve_parameters
from app.modules.read_snapshots.repository import (
    DASHBOARD_INTERVAL_SECONDS,
    REPORT_INTERVAL_SECONDS,
    SnapshotCapacityError,
    SnapshotParametersTooLargeError,
    SnapshotRepository,
    encode_parameters,
)


class SnapshotWarmingError(DashboardServiceUnavailableError):
    code = "read_snapshot_warming"
    message = "Prepared data is warming in the background. No request-time calculation is running."


class SnapshotReader:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = SnapshotRepository(session)

    async def watch_timezone(self, zone: str | None) -> bool:
        if zone == "UTC":
            return True
        if not zone or len(zone) > 100:
            return False
        try:
            ZoneInfo(zone)
        except (ValueError, ZoneInfoNotFoundError):
            return False
        try:
            await self.repository.register("report-zone", {"timezone": zone})
        except SnapshotCapacityError:
            # A preference hint cannot make an already-ready dashboard fail.
            return False
        return True

    async def _read(self, kind: str, parameters: dict[str, JsonValue], interval: int) -> dict[str, JsonValue]:
        try:
            key, _ = encode_parameters(kind, parameters)
        except SnapshotParametersTooLargeError as exc:
            raise DashboardBadRequestError(str(exc), code="read_snapshot_filters_too_large") from exc
        value = await self.repository.read(key, interval_seconds=interval)
        if value is None:
            try:
                await self.repository.register(
                    kind,
                    parameters,
                    pinned=kind == "dashboard" or (kind == "report-preset" and parameters.get("timezone") == "UTC"),
                )
            except SnapshotCapacityError as exc:
                raise DashboardServiceUnavailableError(str(exc), code="read_snapshot_queue_full") from exc
        if value is None or value.payload is None:
            details: dict[str, JsonValue] = {"retryAfterSeconds": 2}
            if value is not None:
                details["snapshot"] = value.metadata()
                if value.last_error is not None:
                    raise DashboardServiceUnavailableError(
                        "Background preparation failed; an automatic retry is scheduled.",
                        code="read_snapshot_failed",
                        details=details,
                    )
            raise SnapshotWarmingError(details=details)
        result = deepcopy(value.payload)
        result["snapshot"] = value.metadata()
        return result

    async def dashboard(
        self,
        *,
        timeframe: DashboardOverviewTimeframeKey | None,
        redact_identity: bool = False,
        timezone_hint: str | None = None,
    ) -> dict[str, JsonValue]:
        await self.watch_timezone(timezone_hint)
        bundle = await self._read("dashboard", {}, DASHBOARD_INTERVAL_SECONDS)
        projections = cast(dict[str, JsonValue], bundle["projections"])
        if timeframe is None:
            result = projections
        else:
            overviews = cast(dict[str, JsonValue], bundle["overviews"])
            result = cast(dict[str, JsonValue], overviews[timeframe])
            if redact_identity:
                for item in cast(list[dict[str, JsonValue]], result["accounts"]):
                    email = item.get("email")
                    if isinstance(email, str):
                        item["email"] = mask_email(email)
                        item["displayName"] = item.get("alias") or item["email"]
                    for name in ("chatgptAccountId", "workspaceId", "workspaceLabel"):
                        item[name] = None
        result["snapshot"] = bundle["snapshot"]
        return result

    async def report(
        self,
        section: str,
        *,
        start: date | None,
        end: date | None,
        timezone: str | None,
        accounts: list[str] | None = None,
        api_keys: list[str] | None = None,
        model: str | None = None,
        useragent: str | None = None,
    ) -> dict[str, JsonValue]:
        kind, parameters, expected_dates = report_parameters(start, end, timezone, accounts, api_keys, model, useragent)
        watched = await self.watch_timezone(expected_dates["timezone"])
        if kind == "report-preset" and not watched:
            # The preset catalog is reserved for admitted viewer timezones.
            # An overflow viewer can still request an exact dated custom view,
            # rather than occupying a preset slot no worker will ever service.
            kind, parameters = "report-range", resolve_parameters(parameters)
        bundle = await self._read(kind, parameters, REPORT_INTERVAL_SECONDS)
        if bundle.get("range") != expected_dates:
            # A rolling preset's previous-day value cannot masquerade as today.
            raise SnapshotWarmingError("The background worker is preparing the new report date range")
        if section not in bundle:
            raise SnapshotWarmingError("The background worker is preparing this report section")
        result = cast(dict[str, JsonValue], bundle[section])
        result["snapshot"] = bundle["snapshot"]
        return result
