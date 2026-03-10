from __future__ import annotations

import json

import pytest

from app.core.usage.models import AdditionalRateLimitPayload, RateLimitPayload, UsagePayload

pytestmark = pytest.mark.unit


def test_additional_rate_limit_parses_valid():
    data = {
        "limit_name": "codex_other",
        "metered_feature": "codex_other",
        "rate_limit": {"primary_window": {"used_percent": 70}},
    }
    payload = AdditionalRateLimitPayload.model_validate(data)
    assert payload.limit_name == "codex_other"
    assert payload.metered_feature == "codex_other"
    assert payload.rate_limit is not None
    assert payload.rate_limit.primary_window is not None
    assert payload.rate_limit.primary_window.used_percent == 70


def test_additional_rate_limit_with_null_rate_limit():
    data = {
        "limit_name": "codex_other",
        "metered_feature": "codex_other",
        "rate_limit": None,
    }
    payload = AdditionalRateLimitPayload.model_validate(data)
    assert payload.rate_limit is None


def test_additional_rate_limit_extra_fields_ignored():
    data = {
        "limit_name": "foo",
        "metered_feature": "foo",
        "rate_limit": None,
        "unknown_field": "bar",
    }
    payload = AdditionalRateLimitPayload.model_validate(data)
    assert not hasattr(payload, "unknown_field")


def test_usage_payload_with_additional_rate_limits():
    data = {
        "plan_type": "pro",
        "rate_limit": {"primary_window": {"used_percent": 45}},
        "additional_rate_limits": [
            {
                "limit_name": "codex_other",
                "metered_feature": "codex_other",
                "rate_limit": {
                    "primary_window": {"used_percent": 70},
                    "secondary_window": None,
                },
            }
        ],
    }
    payload = UsagePayload.model_validate(data)
    assert payload.additional_rate_limits is not None
    assert len(payload.additional_rate_limits) == 1
    assert payload.additional_rate_limits[0].limit_name == "codex_other"


def test_usage_payload_additional_rate_limits_null():
    data = {
        "plan_type": "pro",
        "rate_limit": {"primary_window": {"used_percent": 45}},
        "additional_rate_limits": None,
    }
    payload = UsagePayload.model_validate(data)
    assert payload.additional_rate_limits is None


def test_usage_payload_additional_rate_limits_empty_list():
    data = {
        "plan_type": "pro",
        "rate_limit": {"primary_window": {"used_percent": 45}},
        "additional_rate_limits": [],
    }
    payload = UsagePayload.model_validate(data)
    assert payload.additional_rate_limits == []


def test_usage_payload_without_additional_rate_limits_key():
    # Backward compat: field absent entirely — should default to None
    data = {"plan_type": "pro", "rate_limit": {"primary_window": {"used_percent": 45}}}
    payload = UsagePayload.model_validate(data)
    assert payload.additional_rate_limits is None


def test_usage_payload_additional_reuses_rate_limit_payload():
    """Each additional limit's rate_limit should be a RateLimitPayload instance"""
    data = {
        "plan_type": "pro",
        "additional_rate_limits": [
            {
                "limit_name": "codex_other",
                "metered_feature": "codex_other",
                "rate_limit": {
                    "primary_window": {
                        "used_percent": 55,
                        "reset_at": 1741500000,
                    }
                },
            }
        ],
    }
    payload = UsagePayload.model_validate(data)
    assert payload.additional_rate_limits is not None
    assert isinstance(payload.additional_rate_limits[0].rate_limit, RateLimitPayload)
    assert payload.additional_rate_limits[0].rate_limit.primary_window is not None
    assert payload.additional_rate_limits[0].rate_limit.primary_window.reset_at == 1741500000


def test_usage_payload_parses_fixture():
    """Parse the real fixture file captured from Task 0"""
    import os

    fixture_path = os.path.join(os.path.dirname(__file__), "../../tests/fixtures/upstream_usage_with_additional.json")
    # Try both relative paths
    if not os.path.exists(fixture_path):
        fixture_path = "tests/fixtures/upstream_usage_with_additional.json"
    with open(fixture_path) as f:
        data = json.load(f)
    # Strip the _fixture_notes meta key (not part of API response)
    data.pop("_fixture_notes", None)
    payload = UsagePayload.model_validate(data)
    assert payload.plan_type is not None
    assert payload.additional_rate_limits is not None
    assert len(payload.additional_rate_limits) >= 1
    assert payload.additional_rate_limits[0].limit_name == "codex_other"
