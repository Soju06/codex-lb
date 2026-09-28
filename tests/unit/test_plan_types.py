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


def test_self_serve_business_prolite_alias_canonicalizes_to_prolite():
    upstream_plan = " SELF_SERVE_BUSINESS_PROLITE "

    assert normalize_account_plan_type(upstream_plan) == "prolite"
    assert canonicalize_account_plan_type(upstream_plan) == "prolite"
    assert coerce_account_plan_type(upstream_plan, "free") == "prolite"
    assert normalize_rate_limit_plan_type(upstream_plan) == "prolite"
    assert account_plan_matches_allowed(upstream_plan, frozenset({"pro"})) is True


def test_unknown_plan_passes_when_explicitly_allowed():
    assert account_plan_matches_allowed("future_plan", frozenset({"future_plan", "plus"})) is True


def test_unknown_plan_matching_is_case_insensitive_and_trims_account_value():
    assert account_plan_matches_allowed(" Future_Plan ", frozenset({"future_plan"})) is True


def test_unknown_plan_blocked_when_not_explicitly_allowed():
    assert account_plan_matches_allowed("future_plan", frozenset({"plus"})) is False
