from __future__ import annotations

import json

import pytest

from app.core.usage.live_snapshots import pooled_rate_limit_event_text

pytestmark = pytest.mark.unit

POOL_HEADERS = {
    "x-codex-primary-used-percent": "4.0",
    "x-codex-primary-window-minutes": "300",
    "x-codex-primary-reset-at": "1795700000",
    "x-codex-secondary-used-percent": "9.0",
    "x-codex-secondary-window-minutes": "10080",
    "x-codex-secondary-reset-at": "1796300000",
    "x-codex-credits-has-credits": "true",
    "x-codex-credits-unlimited": "false",
    "x-codex-credits-balance": "12.50",
}

ACCOUNT_EVENT = {
    "type": "codex.rate_limits",
    "plan_type": "pro",
    "rate_limits": {
        "allowed": True,
        "limit_reached": False,
        "primary": {"used_percent": 100.0, "window_minutes": 300, "reset_at": 1795706807},
        "secondary": {"used_percent": 49.0, "window_minutes": 10080, "reset_at": 1795936293},
    },
    "credits": {"has_credits": False, "unlimited": False, "balance": "0"},
}


def test_default_family_event_carries_the_pool_quota() -> None:
    rewritten = json.loads(pooled_rate_limit_event_text(json.dumps(ACCOUNT_EVENT), POOL_HEADERS))

    assert rewritten == {
        "type": "codex.rate_limits",
        "plan_type": "pro",
        "rate_limits": {
            "allowed": True,
            "limit_reached": False,
            "primary": {"used_percent": 4.0, "window_minutes": 300, "reset_at": 1795700000},
            "secondary": {"used_percent": 9.0, "window_minutes": 10080, "reset_at": 1796300000},
        },
        "credits": {"has_credits": True, "unlimited": False, "balance": "12.50"},
    }


def test_windows_the_pool_does_not_report_are_removed() -> None:
    weekly_only = {key: value for key, value in POOL_HEADERS.items() if "-primary-" not in key and "credits" not in key}

    rewritten = json.loads(pooled_rate_limit_event_text(json.dumps(ACCOUNT_EVENT), weekly_only))

    assert "primary" not in rewritten["rate_limits"]
    assert rewritten["rate_limits"]["secondary"]["used_percent"] == 9.0
    assert "credits" not in rewritten


def test_hidden_or_missing_pool_quota_leaves_no_windows() -> None:
    rewritten = json.loads(pooled_rate_limit_event_text(json.dumps(ACCOUNT_EVENT), {}))

    assert rewritten["rate_limits"] == {"allowed": True, "limit_reached": False}
    assert "credits" not in rewritten
    assert rewritten["plan_type"] == "pro"


@pytest.mark.parametrize("family", [{"metered_limit_name": "codex_other"}, {"limit_id": "gpt-5.5"}])
def test_other_limit_families_pass_through(family: dict[str, str]) -> None:
    text = json.dumps({**ACCOUNT_EVENT, **family})

    assert pooled_rate_limit_event_text(text, POOL_HEADERS) == text


@pytest.mark.parametrize(
    "text",
    [
        '{"type":"response.created","response":{"id":"resp_1"}}',
        'not json "codex.rate_limits"',
        '{"type":"response.output_text.delta","delta":"\\"codex.rate_limits\\""}',
    ],
)
def test_other_frames_pass_through(text: str) -> None:
    assert pooled_rate_limit_event_text(text, POOL_HEADERS) == text
