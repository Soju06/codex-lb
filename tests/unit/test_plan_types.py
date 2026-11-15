from __future__ import annotations

import pytest

from app.core.plan_types import (
    account_plan_matches_allowed,
    canonicalize_account_plan_type,
    coerce_account_plan_type,
    normalize_account_plan_type,
    normalize_rate_limit_plan_type,
)

pytestmark = pytest.mark.unit


def test_prolite_matches_pro_model_plan_entitlement():
    assert account_plan_matches_allowed("prolite", frozenset({"pro"})) is True
    assert account_plan_matches_allowed("prolite", frozenset({"plus"})) is False


def test_unknown_plan_passes_when_explicitly_allowed():
    assert account_plan_matches_allowed("future_plan", frozenset({"future_plan", "plus"})) is True


def test_unknown_plan_matching_is_case_insensitive_and_trims_account_value():
    assert account_plan_matches_allowed(" Future_Plan ", frozenset({"future_plan"})) is True


def test_unknown_plan_blocked_when_not_explicitly_allowed():
    assert account_plan_matches_allowed("future_plan", frozenset({"plus"})) is False


@pytest.mark.parametrize("value", ["self_serve_business_prolite", " SELF_SERVE_BUSINESS_PROLITE "])
def test_business_prolite_alias_is_canonical_at_plan_boundaries(value):
    assert normalize_account_plan_type(value) == "prolite"
    assert canonicalize_account_plan_type(value) == "prolite"
    assert coerce_account_plan_type(value, "free") == "prolite"
    assert normalize_rate_limit_plan_type(value) == "prolite"
    assert account_plan_matches_allowed(value, frozenset({"pro"})) is True
    assert account_plan_matches_allowed("prolite", frozenset({value})) is True
    assert account_plan_matches_allowed(value, frozenset({"plus"})) is False


def test_promax_is_recognized_without_losing_its_identifier():
    assert normalize_account_plan_type(" ProMax ") == "promax"
    assert canonicalize_account_plan_type(" ProMax ") == "promax"
    assert normalize_rate_limit_plan_type("promax") == "promax"
    assert account_plan_matches_allowed("promax", frozenset({"pro"})) is True
    assert account_plan_matches_allowed("promax", frozenset({"plus"})) is False
