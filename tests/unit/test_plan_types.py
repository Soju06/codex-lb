from __future__ import annotations

import pytest

from app.core.plan_types import (
    account_plan_matches_allowed,
    coerce_account_plan_type,
    normalize_account_plan_type,
    normalize_rate_limit_plan_type,
)

pytestmark = pytest.mark.unit


def test_prolite_matches_pro_model_plan_entitlement():
    assert account_plan_matches_allowed("prolite", frozenset({"pro"})) is True
    assert account_plan_matches_allowed("prolite", frozenset({"plus"})) is False


def test_promax_preserves_identity_and_pro_family_entitlement():
    assert normalize_account_plan_type(" ProMax ") == "promax"
    assert normalize_rate_limit_plan_type(" ProMax ") == "promax"
    assert coerce_account_plan_type(" ProMax ", default="plus") == "promax"
    assert account_plan_matches_allowed("promax", frozenset({"pro"})) is True
    assert account_plan_matches_allowed("promax", frozenset({"promax"})) is True
    assert account_plan_matches_allowed("promax", frozenset({"plus"})) is False
    assert account_plan_matches_allowed("pro", frozenset({"promax"})) is False
    assert account_plan_matches_allowed("prolite", frozenset({"promax"})) is False


def test_unknown_plan_passes_when_explicitly_allowed():
    assert account_plan_matches_allowed("future_plan", frozenset({"future_plan", "plus"})) is True


def test_unknown_plan_matching_is_case_insensitive_and_trims_account_value():
    assert account_plan_matches_allowed(" Future_Plan ", frozenset({"future_plan"})) is True


def test_unknown_plan_blocked_when_not_explicitly_allowed():
    assert account_plan_matches_allowed("future_plan", frozenset({"plus"})) is False
