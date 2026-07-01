"""Anthropic rate-limit response header parser.

Anthropic emits a fixed set of ``anthropic-ratelimit-*`` headers on every
response (both 200 and 429). The verified contract is documented in
``openspec/changes/add-claude-oauth-pool/notes.md`` §4:

- ``anthropic-ratelimit-{requests,input-tokens,output-tokens}-remaining`` —
  integer (requests or tokens remaining in the current window).
- ``anthropic-ratelimit-{requests,input-tokens,output-tokens}-reset`` —
  absolute RFC 3339 timestamp (e.g. ``2026-07-01T12:00:00Z``).
  Relative form (``"in 5m"``) and bare unix seconds have not been observed
  and are intentionally not parsed — the parser drops malformed reset values
  rather than guessing.
- ``anthropic-ratelimit-status`` — string (``allowed`` / ``allowed_warning``
  / ``rejected`` / ``limited``).

The output schema mirrors the snake_case column names on the ``Account``
model so the load-balancer cooldown path can persist the parsed result
without renaming keys:

    {
        "rate_limit_requests_remaining": int,
        "rate_limit_requests_reset_at": datetime,    # tz-aware UTC
        "rate_limit_input_tokens_remaining": int,
        "rate_limit_input_tokens_reset_at": datetime,
        "rate_limit_output_tokens_remaining": int,
        "rate_limit_output_tokens_reset_at": datetime,
        "rate_limit_status": str,
    }

Missing or malformed values are simply absent from the returned dict; the
parser never raises. This keeps the request handler robust against partial
or unexpected header sets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping

# Integer-valued headers: header name -> output key.
_REMAINING_KEY_MAP: dict[str, str] = {
    "anthropic-ratelimit-requests-remaining": "rate_limit_requests_remaining",
    "anthropic-ratelimit-input-tokens-remaining": "rate_limit_input_tokens_remaining",
    "anthropic-ratelimit-output-tokens-remaining": "rate_limit_output_tokens_remaining",
}

# Reset headers: header name -> output key. Per notes.md §4, these are absolute
# RFC 3339 timestamps; there is no relative-form fallback.
_RESET_KEY_MAP: dict[str, str] = {
    "anthropic-ratelimit-requests-reset": "rate_limit_requests_reset_at",
    "anthropic-ratelimit-input-tokens-reset": "rate_limit_input_tokens_reset_at",
    "anthropic-ratelimit-output-tokens-reset": "rate_limit_output_tokens_reset_at",
}

# Status header carries a free-form string, passed through unchanged.
_STATUS_KEY = ("anthropic-ratelimit-status", "rate_limit_status")


def parse_anthropic_rate_limit_headers(
    headers: Mapping[str, str],
) -> dict[str, object]:
    """Return a dict of parsed rate-limit fields.

    Only headers that are present AND parseable appear in the output. The
    function never raises on malformed input — that would defeat its
    purpose as a non-blocking post-response observability hook.
    """
    out: dict[str, object] = {}

    for src, dst in _REMAINING_KEY_MAP.items():
        raw = headers.get(src)
        if raw is None:
            continue
        try:
            out[dst] = int(raw)
        except ValueError:
            # Bad integer — drop this header only.
            continue

    for src, dst in _RESET_KEY_MAP.items():
        raw = headers.get(src)
        if raw is None:
            continue
        parsed = _parse_reset(raw)
        if parsed is not None:
            out[dst] = parsed

    src, dst = _STATUS_KEY
    raw = headers.get(src)
    if raw is not None:
        out[dst] = raw

    return out


def _parse_reset(raw: str) -> datetime | None:
    """Parse an RFC 3339 / ISO 8601 reset timestamp.

    Accepts the ``Z`` UTC suffix (which ``datetime.fromisoformat`` rejects
    on Python <3.11). Returns a timezone-aware datetime in UTC, or ``None``
    if the value is not a parseable RFC 3339 timestamp.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Python's fromisoformat handles offsets but historically rejected ``Z``.
    # Normalize to ``+00:00`` for compatibility.
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # Per RFC 3339, a missing offset is ambiguous; treat as UTC defensively
        # since Anthropic emits absolute timestamps.
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt