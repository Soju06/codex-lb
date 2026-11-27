from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Mapping

_HEADER_PREFIX = "x-codex-"
_EVENT_TYPE = "codex.rate_limits"
# Cheap containment probe so stream hot paths can skip JSON parsing for the
# overwhelming majority of event blocks.
EVENT_MARKER = '"codex.rate_limits"'


@dataclass(frozen=True, slots=True)
class LiveUsageWindow:
    used_percent: float
    window_minutes: int | None
    reset_at: int | None


@dataclass(frozen=True, slots=True)
class LiveRateLimitSnapshot:
    primary: LiveUsageWindow | None
    secondary: LiveUsageWindow | None
    credits_has: bool | None
    credits_unlimited: bool | None
    credits_balance: float | None

    @property
    def has_windows(self) -> bool:
        return self.primary is not None or self.secondary is not None


def _parse_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    # Live signals run synchronously on the serving path; non-finite values
    # (NaN/Infinity strings) must degrade to "unparseable", not raise later
    # in int() coercion.
    if not math.isfinite(parsed):
        return None
    return parsed


def _parse_int(value: object) -> int | None:
    parsed = _parse_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1"}:
            return True
        if lowered in {"false", "0"}:
            return False
    return None


def _header_window(headers: Mapping[str, str], slot: str) -> LiveUsageWindow | None:
    used = _parse_float(headers.get(f"{_HEADER_PREFIX}{slot}-used-percent"))
    if used is None:
        return None
    return LiveUsageWindow(
        used_percent=used,
        window_minutes=_parse_int(headers.get(f"{_HEADER_PREFIX}{slot}-window-minutes")),
        reset_at=_parse_int(headers.get(f"{_HEADER_PREFIX}{slot}-reset-at")),
    )


def parse_rate_limit_headers(headers: Mapping[str, str] | None) -> LiveRateLimitSnapshot | None:
    """Parse upstream ``x-codex-*`` rate-limit response headers.

    Only the default ``codex`` limit family is consumed; additional
    ``x-<limit-id>-*`` families are out of scope for live ingestion.
    """
    if not headers:
        return None
    lowered = {key.lower(): value for key, value in headers.items()}
    primary = _header_window(lowered, "primary")
    secondary = _header_window(lowered, "secondary")
    if primary is None and secondary is None:
        return None
    return LiveRateLimitSnapshot(
        primary=primary,
        secondary=secondary,
        credits_has=_parse_bool(lowered.get(f"{_HEADER_PREFIX}credits-has-credits")),
        credits_unlimited=_parse_bool(lowered.get(f"{_HEADER_PREFIX}credits-unlimited")),
        credits_balance=_parse_float(lowered.get(f"{_HEADER_PREFIX}credits-balance")),
    )


def _event_window(payload: object) -> LiveUsageWindow | None:
    if not isinstance(payload, dict):
        return None
    used = _parse_float(payload.get("used_percent"))
    if used is None:
        return None
    return LiveUsageWindow(
        used_percent=used,
        window_minutes=_parse_int(payload.get("window_minutes")),
        reset_at=_parse_int(
            payload.get("reset_at") if payload.get("reset_at") is not None else payload.get("resets_at")
        ),
    )


def parse_rate_limit_event(payload: Mapping[str, Any]) -> LiveRateLimitSnapshot | None:
    """Parse a ``codex.rate_limits`` stream event payload."""
    if payload.get("type") != _EVENT_TYPE:
        return None
    # Only the default ``codex`` limit family maps onto the account's main
    # usage rows; events for model-specific or individual limit buckets
    # (discriminated by limit_id / metered_limit_name / limit_name) must not
    # corrupt global selection state.
    if not _is_default_limit_family(payload):
        return None
    rate_limits = payload.get("rate_limits")
    if not isinstance(rate_limits, dict):
        return None
    primary = _event_window(rate_limits.get("primary"))
    secondary = _event_window(rate_limits.get("secondary"))
    if primary is None and secondary is None:
        return None
    credits = payload.get("credits")
    if not isinstance(credits, dict):
        credits = rate_limits.get("credits")
    if not isinstance(credits, dict):
        credits = {}
    return LiveRateLimitSnapshot(
        primary=primary,
        secondary=secondary,
        credits_has=_parse_bool(credits.get("has_credits")),
        credits_unlimited=_parse_bool(credits.get("unlimited")),
        credits_balance=_parse_float(credits.get("balance")),
    )


def _is_default_limit_family(payload: Mapping[str, Any]) -> bool:
    return all(payload.get(key) in (None, "codex") for key in ("limit_id", "metered_limit_name", "limit_name"))


def _event_window_payload(window: LiveUsageWindow) -> dict[str, Any]:
    return {
        "used_percent": window.used_percent,
        "window_minutes": window.window_minutes,
        "reset_at": window.reset_at,
    }


def pooled_rate_limit_event_text(text: str, pool_headers: Mapping[str, str]) -> str:
    """Return a WebSocket ``codex.rate_limits`` frame with the pool's quota.

    Upstream reports the account that served the turn; the client reads the
    event as its endpoint's quota, so the default family carries the aggregate
    the proxy's ``x-codex-*`` headers report, and nothing when they report none.
    """
    if EVENT_MARKER not in text:
        return text
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(payload, dict) or payload.get("type") != _EVENT_TYPE or not _is_default_limit_family(payload):
        return text
    pool = parse_rate_limit_headers(pool_headers)
    rate_limits = payload.get("rate_limits")
    rate_limits = dict(rate_limits) if isinstance(rate_limits, dict) else {}
    rate_limits.pop("credits", None)
    payload.pop("credits", None)
    for slot in ("primary", "secondary"):
        window = getattr(pool, slot) if pool is not None else None
        if window is None:
            rate_limits.pop(slot, None)
        else:
            rate_limits[slot] = _event_window_payload(window)
    payload["rate_limits"] = rate_limits
    if pool is not None and pool.credits_has is not None and pool.credits_unlimited is not None:
        payload["credits"] = {
            "has_credits": pool.credits_has,
            "unlimited": pool.credits_unlimited,
            "balance": None if pool.credits_balance is None else f"{pool.credits_balance:.2f}",
        }
    return json.dumps(payload, separators=(",", ":"))


def parse_rate_limit_event_text(text: str) -> LiveRateLimitSnapshot | None:
    """Parse a raw stream chunk that may contain a rate-limit event.

    Accepts either a bare JSON object or an SSE ``data:`` block. Callers
    should gate on :data:`EVENT_MARKER` first to keep hot paths cheap.
    """
    if EVENT_MARKER not in text:
        return None
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("data:"):
            candidate = candidate[5:].strip()
        if not candidate.startswith("{") or EVENT_MARKER not in candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            snapshot = parse_rate_limit_event(payload)
            if snapshot is not None:
                return snapshot
    return None
