from __future__ import annotations

import pytest

from app.db.models import LimitType, LimitWindow
from app.modules.api_keys.service import (
    ApiKeyValidationError,
    LimitRuleInput,
    _build_limit_rows_for_update,
    _limit_input_to_row,
    _normalize_name,
    _normalize_reasoning_effort,
    _normalize_service_tier,
    _validate_model_enforcement,
)

pytestmark = pytest.mark.unit


# Regression for #619: every user-facing api_keys validation failure must
# raise the typed ApiKeyValidationError so API routes can catch the
# typed exception and unrelated programming errors that happen to be
# ValueError no longer get silently converted into HTTP 4xx.


def test_normalize_name_raises_typed_validation_error_on_blank_input() -> None:
    with pytest.raises(ApiKeyValidationError):
        _normalize_name("   ")


def test_normalize_reasoning_effort_raises_typed_validation_error_on_unknown_value() -> None:
    with pytest.raises(ApiKeyValidationError) as info:
        _normalize_reasoning_effort("balanced")
    assert "balanced" in str(info.value)


def test_normalize_service_tier_raises_typed_validation_error_on_unknown_value() -> None:
    with pytest.raises(ApiKeyValidationError) as info:
        _normalize_service_tier("ludicrous")
    assert "ludicrous" in str(info.value)


def test_validate_model_enforcement_raises_typed_validation_error_when_enforced_not_in_allowed() -> None:
    with pytest.raises(ApiKeyValidationError):
        _validate_model_enforcement(
            enforced_model="gpt-5.5",
            allowed_models=["gpt-5"],
        )


def test_limit_input_to_row_raises_typed_validation_error_for_credits_with_model_filter() -> None:
    rule = LimitRuleInput(
        limit_type=LimitType.CREDITS.value,
        limit_window=LimitWindow.DAILY.value,
        max_value=100,
        model_filter="gpt-5",
    )
    with pytest.raises(ApiKeyValidationError) as info:
        _limit_input_to_row(rule, key_id="key-1", now=__import__("datetime").datetime(2026, 5, 15))
    assert "credits" in str(info.value).lower()


def test_build_limit_rows_for_update_raises_typed_validation_error_on_duplicate_rules() -> None:
    rule = LimitRuleInput(
        limit_type=LimitType.TOTAL_TOKENS.value,
        limit_window=LimitWindow.DAILY.value,
        max_value=100,
        model_filter=None,
    )
    with pytest.raises(ApiKeyValidationError) as info:
        _build_limit_rows_for_update(
            key_id="key-1",
            now=__import__("datetime").datetime(2026, 5, 15),
            submitted_limits=[rule, rule],
            existing_limits=[],
            reset_usage=False,
        )
    assert "duplicate" in str(info.value).lower()


def test_api_key_validation_error_is_value_error_subclass_for_back_compat() -> None:
    # Existing transitive callers that catch ValueError must continue
    # to work; only the api routes have been narrowed to the typed
    # exception. Subclass relationship preserves that contract.
    assert issubclass(ApiKeyValidationError, ValueError)
