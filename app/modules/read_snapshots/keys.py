from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.types import JsonValue
from app.core.utils.time import utcnow
from app.modules.reports.repository import MAX_DAILY_REPORT_DAYS
from app.modules.reports.service import InvalidReportDateRangeError, resolve_report_range

REPORT_PRESETS = (7, 30, 90)
DASHBOARD_RANGES = ("1d", "7d", "30d")


def local_today(zone: str, now: datetime | None = None) -> date:
    from datetime import timezone

    clock = (now or utcnow()).replace(tzinfo=timezone.utc)
    return clock.astimezone(ZoneInfo(zone)).date()


def report_parameters(
    start: date | None,
    end: date | None,
    zone: str | None,
    accounts: list[str] | None = None,
    api_keys: list[str] | None = None,
    model: str | None = None,
    useragent: str | None = None,
) -> tuple[str, dict[str, JsonValue], dict[str, str]]:
    start, end, timezone_info = resolve_report_range(start, end, zone)
    days = (end - start).days + 1
    if days > MAX_DAILY_REPORT_DAYS:
        raise InvalidReportDateRangeError(f"Report ranges cannot exceed {MAX_DAILY_REPORT_DAYS} days")
    timezone_name = str(timezone_info)
    dates: dict[str, str] = {"start": start.isoformat(), "end": end.isoformat(), "timezone": timezone_name}
    if not any((accounts, api_keys, model, useragent)) and days in REPORT_PRESETS and end == local_today(timezone_name):
        return "report-preset", {"days": days, "timezone": timezone_name}, dates
    return (
        "report-range",
        {
            **dates,
            "accounts": sorted(set(accounts or [])),
            "apiKeys": sorted(set(api_keys or [])),
            "model": model or "",
            "useragent": useragent or "",
        },
        dates,
    )


def resolve_parameters(parameters: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if "days" not in parameters:
        return parameters
    zone = str(parameters["timezone"])
    today = local_today(zone)
    days = int(str(parameters["days"]))
    if days not in REPORT_PRESETS:
        raise ValueError("Unsupported report preset")
    return {
        "start": (today - timedelta(days=days - 1)).isoformat(),
        "end": today.isoformat(),
        "timezone": zone,
        "accounts": [],
        "apiKeys": [],
        "model": "",
        "useragent": "",
    }
