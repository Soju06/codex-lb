from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from app.core.config.settings_cache import get_settings_cache
from app.core.types import JsonValue
from app.core.usage.live_snapshots import LiveUsageWindow, parse_rate_limit_headers
from app.modules.api_keys.service import ApiKeyData


async def hide_upstream_quota_for_api_key_clients(api_key: ApiKeyData | None) -> bool:
    if api_key is None:
        return False
    settings = await get_settings_cache().get()
    return settings.hide_upstream_quota_from_api_keys


async def rate_limit_headers_for_client(
    api_key: ApiKeyData | None,
    load_headers: Callable[[], Awaitable[dict[str, str]]],
) -> dict[str, str]:
    if await hide_upstream_quota_for_api_key_clients(api_key):
        return {}
    return await load_headers()


def project_codex_rate_limit_event(
    payload: Mapping[str, JsonValue],
    headers: Mapping[str, str],
) -> dict[str, JsonValue] | None:
    """Replace account telemetry with the downstream pool snapshot, never merge it."""
    for discriminator in ("limit_id", "metered_limit_name", "limit_name"):
        if payload.get(discriminator) not in (None, "codex"):
            return None
    snapshot = parse_rate_limit_headers(headers)
    if snapshot is None or not snapshot.has_windows:
        return None
    event: dict[str, JsonValue] = {
        "type": "codex.rate_limits",
        "rate_limits": {
            "primary": _event_window(snapshot.primary),
            "secondary": _event_window(snapshot.secondary),
        },
    }
    if snapshot.credits_has is not None and snapshot.credits_unlimited is not None:
        event["credits"] = {
            "has_credits": snapshot.credits_has,
            "unlimited": snapshot.credits_unlimited,
            "balance": headers.get("x-codex-credits-balance"),
        }
    return event


def _event_window(window: LiveUsageWindow | None) -> dict[str, JsonValue] | None:
    if window is None:
        return None
    return {
        "used_percent": window.used_percent,
        "window_minutes": window.window_minutes,
        "reset_at": window.reset_at,
    }
