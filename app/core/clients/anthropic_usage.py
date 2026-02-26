from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

import aiohttp

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings


class AnthropicUsageFetchError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True, slots=True)
class AnthropicUsageWindow:
    used_percent: float
    reset_at_epoch: int | None
    window_minutes: int


@dataclass(frozen=True, slots=True)
class AnthropicUsageSnapshot:
    five_hour: AnthropicUsageWindow | None
    seven_day: AnthropicUsageWindow | None


async def fetch_usage_snapshot(
    *,
    bearer_token: str,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> AnthropicUsageSnapshot:
    settings = get_settings()
    usage_base = (base_url or settings.anthropic_usage_base_url).rstrip("/")
    url = f"{usage_base}/api/oauth/usage"
    timeout = aiohttp.ClientTimeout(total=settings.usage_fetch_timeout_seconds)
    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "anthropic-beta": settings.anthropic_usage_beta,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    client = session or get_http_client().session
    try:
        async with client.get(url, headers=headers, timeout=timeout) as resp:
            payload = await _safe_json(resp)
            if resp.status >= 400:
                raise AnthropicUsageFetchError(
                    resp.status,
                    _error_message(payload) or f"Usage fetch failed ({resp.status})",
                )
    except AnthropicUsageFetchError:
        raise
    except aiohttp.ClientError as exc:
        raise AnthropicUsageFetchError(0, f"Usage fetch failed: {exc}") from exc

    return _parse_usage_payload(payload)


async def _safe_json(resp: aiohttp.ClientResponse) -> dict[str, object]:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _error_message(payload: dict[str, object]) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict):
        error_payload = cast(dict[str, object], error)
        message = error_payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    message = payload.get("message")
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


def _parse_usage_payload(payload: dict[str, object]) -> AnthropicUsageSnapshot:
    five_hour = _parse_usage_window(payload.get("five_hour"), window_minutes=300)
    seven_day = _parse_usage_window(payload.get("seven_day"), window_minutes=10080)
    return AnthropicUsageSnapshot(five_hour=five_hour, seven_day=seven_day)


def _parse_usage_window(raw: object, *, window_minutes: int) -> AnthropicUsageWindow | None:
    if not isinstance(raw, dict):
        return None
    raw_payload = cast(dict[str, object], raw)
    used = _normalize_utilization_percent(raw_payload.get("utilization"))
    if used is None:
        return None
    reset_at_epoch = _parse_reset_at(raw_payload.get("resets_at"))
    return AnthropicUsageWindow(
        used_percent=used,
        reset_at_epoch=reset_at_epoch,
        window_minutes=window_minutes,
    )


def _normalize_utilization_percent(value: object) -> float | None:
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 1.0:
            numeric *= 100.0
        return max(0.0, min(100.0, numeric))
    return None


def _parse_reset_at(value: object) -> int | None:
    if isinstance(value, str):
        parsed = _parse_iso8601(value)
        if parsed is not None:
            return int(parsed.timestamp())
        return None
    if isinstance(value, (int, float)):
        # Some payloads may send epoch seconds.
        return int(value)
    return None


def _parse_iso8601(value: str) -> datetime | None:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
