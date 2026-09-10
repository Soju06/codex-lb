from copy import deepcopy

import pytest

from app.core.types import JsonObject
from app.modules.desktop_usage.composition import compose_desktop_usage
from app.modules.proxy.types import (
    AdditionalRateLimitData,
    RateLimitStatusDetailsData,
    RateLimitStatusPayloadData,
    RateLimitWindowSnapshotData,
)

pytestmark = pytest.mark.unit


def _quota(used: int) -> RateLimitStatusDetailsData:
    return RateLimitStatusDetailsData(
        allowed=used < 100,
        limit_reached=used >= 100,
        primary_window=RateLimitWindowSnapshotData(used, 18000, 1200, 2000000000),
    )


def _pool(used: int = 40, *additional: AdditionalRateLimitData) -> RateLimitStatusPayloadData:
    return RateLimitStatusPayloadData("pro", _quota(used), additional_rate_limits=list(additional))


def test_original_account_envelope_and_inputs_are_preserved() -> None:
    original: JsonObject = {
        "user_id": "original-user",
        "account_id": "original-account",
        "plan_type": "plus",
        "credits": {"has_credits": False, "balance": "0"},
        "spend_control": {"reached": True},
        "effective_monthly_limit": {"limit": "12"},
        "current_month_usage": "10",
        "rate_limit_reset_credits": {"available_count": 2, "owner": "original"},
        "future_metadata": {"nested": [1, "retained"]},
        "rate_limit": {"allowed": False, "limit_reached": True},
    }
    before = deepcopy(original)
    result = compose_desktop_usage(original, _pool())
    assert original == before
    assert {key: value for key, value in result.items() if key != "rate_limit"} == {
        key: value for key, value in original.items() if key != "rate_limit"
    }
    assert result["rate_limit"] == {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 40,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 1200,
            "reset_at": 2000000000,
        },
        "secondary_window": None,
        "monthly_window": None,
    }


def test_additional_aliases_keep_metadata_and_append_new_pool_buckets() -> None:
    reserve: JsonObject = {
        "limit_name": "gpt-reserve",
        "normal_model_slug": "gpt-5.6-luna",
        "rate_limit": {"allowed": True},
    }
    unmatched: JsonObject = {"limit_name": "unknown", "rate_limit": {"allowed": False}}
    original: JsonObject = {
        "additional_rate_limits": [
            {
                "limit_name": " GPT_6.ASTRA ",
                "metered_feature": "codex",
                "normal_model_slug": "original-model",
                "metadata": {"x": 1},
            },
            reserve,
            unmatched,
        ]
    }
    before = deepcopy(original)
    result = compose_desktop_usage(
        original,
        _pool(
            40,
            AdditionalRateLimitData("gpt-6-astra", "codex", rate_limit=_quota(100)),
            AdditionalRateLimitData("gpt-reserve", "codex", rate_limit=_quota(100)),
            AdditionalRateLimitData("gpt-new", "new", display_label="New", rate_limit=_quota(20)),
        ),
    )
    buckets = result["additional_rate_limits"]
    assert isinstance(buckets, list)
    assert len(buckets) == 4
    assert buckets[0] == {
        "limit_name": " GPT_6.ASTRA ",
        "metered_feature": "codex",
        "normal_model_slug": "original-model",
        "metadata": {"x": 1},
        "rate_limit": {
            "allowed": False,
            "limit_reached": True,
            "primary_window": {
                "used_percent": 100,
                "limit_window_seconds": 18000,
                "reset_after_seconds": 1200,
                "reset_at": 2000000000,
            },
            "secondary_window": None,
            "monthly_window": None,
        },
    }
    assert buckets[1:3] == [reserve, unmatched]
    assert buckets[3] == {
        "quota_key": None,
        "limit_name": "gpt-new",
        "display_label": "New",
        "metered_feature": "new",
        "rate_limit": {
            "allowed": True,
            "limit_reached": False,
            "primary_window": {
                "used_percent": 20,
                "limit_window_seconds": 18000,
                "reset_after_seconds": 1200,
                "reset_at": 2000000000,
            },
            "secondary_window": None,
            "monthly_window": None,
        },
    }
    assert original == before


def test_only_evidenced_exhaustion_warnings_are_removed() -> None:
    warning: JsonObject = {"banner_type": "rate_limit", "rate_limit": {"allowed": False}}
    original: JsonObject = {
        "rate_limit_reached_type": {"type": "rate_limit_reached"},
        "rate_limit_upsell": {"banner_type": "luna_reserve"},
        "rate_limit_warning": warning,
        "sidebar_usage_warnings": {
            "default": warning,
            "by_model": {"gpt_6.astra": warning, "unknown": warning},
            "future": "preserved",
        },
    }
    result = compose_desktop_usage(
        original, _pool(40, AdditionalRateLimitData("gpt-6-astra", "codex", rate_limit=_quota(40)))
    )
    assert "rate_limit_reached_type" not in result
    assert "rate_limit_upsell" not in result
    assert "rate_limit_warning" not in result
    assert result["sidebar_usage_warnings"] == {
        "default": None,
        "by_model": {"gpt_6.astra": None, "unknown": warning},
        "future": "preserved",
    }


@pytest.mark.parametrize("marker", ["workspace_owner_credits_depleted", "unknown_restriction"])
@pytest.mark.parametrize("banner", ["credits", "future_banner"])
def test_account_owned_and_unknown_restrictions_are_not_cleared(marker: str, banner: str) -> None:
    original: JsonObject = {
        "rate_limit_reached_type": {"type": marker},
        "rate_limit_upsell": {"banner_type": banner},
        "model_picker_upsell": {"blocked_model_slug": "gpt-6-astra"},
        "rate_limit_warning": {"banner_type": banner},
    }
    result = compose_desktop_usage(original, _pool())
    for key, value in original.items():
        assert result[key] == value


@pytest.mark.parametrize(
    "warning",
    [
        {"banner_type": "rate_limit", "rate_limit": {"allowed": True}},
        {"banner_type": "rate_limit", "rate_limit": {"allowed": False}, "monthly_limit": {"limit": "0"}},
        {"banner_type": "rate_limit", "rate_limit": {"allowed": False}, "credits": {"has_credits": False}},
        {"banner_type": "rate_limit", "rate_limit": {"allowed": False}, "model_slug": "gpt-6-astra"},
    ],
)
def test_warnings_without_equivalent_available_pool_evidence_remain(warning: JsonObject) -> None:
    result = compose_desktop_usage(
        {"rate_limit_warning": warning},
        _pool(40, AdditionalRateLimitData("gpt-6-astra", "codex", rate_limit=_quota(100))),
    )
    assert result["rate_limit_warning"] == warning


def test_exhausted_main_keeps_reserve_and_exhaustion_state() -> None:
    original: JsonObject = {
        "rate_limit_reached_type": {"type": "rate_limit_reached"},
        "rate_limit_upsell": {"banner_type": "luna_reserve"},
    }
    result = compose_desktop_usage(original, _pool(100))
    for key, value in original.items():
        assert result[key] == value


def test_missing_main_pool_quota_is_not_available() -> None:
    with pytest.raises(ValueError, match="requires pooled main quota"):
        compose_desktop_usage({}, RateLimitStatusPayloadData("pro"))


@pytest.mark.parametrize("feature", ["different_feature", None])
def test_same_named_limit_with_unknown_feature_equivalence_stays_restricted(feature: str | None) -> None:
    bucket: JsonObject = {
        "limit_name": "gpt-6-astra",
        "metered_feature": feature,
        "rate_limit": {"allowed": False, "limit_reached": True},
    }
    warning: JsonObject = {"banner_type": "rate_limit", "model_slug": "gpt-6-astra", "rate_limit": {"allowed": False}}
    result = compose_desktop_usage(
        {"additional_rate_limits": [bucket], "rate_limit_warning": warning},
        _pool(40, AdditionalRateLimitData("gpt-6-astra", "codex", rate_limit=_quota(0))),
    )
    assert result["additional_rate_limits"] == [bucket]
    assert result["rate_limit_warning"] == warning
