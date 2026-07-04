"""Tests for ``app.core.clients.anthropic.headers.parse_anthropic_rate_limit_headers``.

Anthropic emits ``anthropic-ratelimit-*`` headers on every response (200 and 429).
Per ``openspec/changes/add-claude-oauth-pool/notes.md`` §4:

- Remaining values are integers.
- Reset values are absolute RFC 3339 timestamps (e.g. ``2026-07-01T12:00:00Z``).
- Status is a string (``allowed`` / ``allowed_warning`` / ``rejected`` / ``limited``).

The parser drops values that cannot be parsed (malformed reset → drop; bad int → drop)
rather than raising, so callers can still persist the rest of the headers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.clients.anthropic.headers import parse_anthropic_rate_limit_headers

pytestmark = pytest.mark.unit


def test_parses_all_headers_present() -> None:
    headers = {
        "anthropic-ratelimit-requests-remaining": "42",
        "anthropic-ratelimit-requests-reset": "2026-07-01T12:00:00Z",
        "anthropic-ratelimit-input-tokens-remaining": "100000",
        "anthropic-ratelimit-input-tokens-reset": "2026-07-01T12:00:00Z",
        "anthropic-ratelimit-output-tokens-remaining": "50000",
        "anthropic-ratelimit-output-tokens-reset": "2026-07-01T12:00:00Z",
        "anthropic-ratelimit-status": "allowed",
    }

    parsed = parse_anthropic_rate_limit_headers(headers)

    assert parsed["rate_limit_requests_remaining"] == 42
    assert parsed["rate_limit_input_tokens_remaining"] == 100000
    assert parsed["rate_limit_output_tokens_remaining"] == 50000
    assert parsed["rate_limit_status"] == "allowed"
    # All three resets parse to the same instant; verify they are timezone-aware UTC.
    expected_dt = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert parsed["rate_limit_requests_reset_at"] == expected_dt
    assert parsed["rate_limit_input_tokens_reset_at"] == expected_dt
    assert parsed["rate_limit_output_tokens_reset_at"] == expected_dt
    assert parsed["rate_limit_requests_reset_at"].tzinfo is not None  # ty:ignore[unresolved-attribute]


def test_missing_headers_returns_only_present_keys() -> None:
    parsed = parse_anthropic_rate_limit_headers({})
    assert parsed == {}


def test_partial_headers_only_present_keys_in_output() -> None:
    headers = {
        "anthropic-ratelimit-requests-remaining": "7",
        "anthropic-ratelimit-status": "rejected",
    }

    parsed = parse_anthropic_rate_limit_headers(headers)

    assert parsed == {
        "rate_limit_requests_remaining": 7,
        "rate_limit_status": "rejected",
    }
    # No reset key when no reset header present.
    assert "rate_limit_requests_reset_at" not in parsed


def test_malformed_reset_value_drops_that_key_keeps_others() -> None:
    headers = {
        "anthropic-ratelimit-requests-reset": "not-a-timestamp",
        "anthropic-ratelimit-input-tokens-remaining": "100",
        "anthropic-ratelimit-input-tokens-reset": "also-not-a-timestamp",
    }

    parsed = parse_anthropic_rate_limit_headers(headers)

    # Malformed resets dropped, remaining integer preserved.
    assert parsed == {"rate_limit_input_tokens_remaining": 100}
    assert "rate_limit_requests_reset_at" not in parsed
    assert "rate_limit_input_tokens_reset_at" not in parsed


def test_z_suffix_in_reset_value_accepted_as_utc() -> None:
    # Z is the canonical RFC 3339 UTC suffix; the parser must accept it and
    # normalize to a tz-aware datetime.
    headers = {"anthropic-ratelimit-requests-reset": "2026-07-01T12:00:00Z"}

    parsed = parse_anthropic_rate_limit_headers(headers)

    assert parsed["rate_limit_requests_reset_at"] == datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_explicit_offset_in_reset_value_accepted() -> None:
    # RFC 3339 with an explicit +00:00 offset should also parse.
    headers = {"anthropic-ratelimit-requests-reset": "2026-07-01T12:00:00+00:00"}

    parsed = parse_anthropic_rate_limit_headers(headers)

    assert parsed["rate_limit_requests_reset_at"] == datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


def test_relative_form_reset_value_is_dropped() -> None:
    # notes.md §4 explicitly states relative form ("in 5m") is never emitted.
    # The parser must drop, not guess.
    parsed = parse_anthropic_rate_limit_headers({"anthropic-ratelimit-requests-reset": "in 5m"})
    assert "rate_limit_requests_reset_at" not in parsed


def test_malformed_integer_remaining_is_dropped() -> None:
    parsed = parse_anthropic_rate_limit_headers({"anthropic-ratelimit-requests-remaining": "not-a-number"})
    assert "rate_limit_requests_remaining" not in parsed
