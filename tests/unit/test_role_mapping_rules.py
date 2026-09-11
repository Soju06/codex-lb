"""Pure rules of the group-to-role mappings: which rule wins, and what a groups header means."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.auth.dashboard_mode import MAX_PROXY_GROUP_LENGTH, MAX_PROXY_GROUPS, parse_proxy_groups
from app.core.config.settings import Settings
from app.db.models import DashboardRoleMapping
from app.modules.role_mappings.matching import email_domain, match_role_id, normalize_claim_value

pytestmark = pytest.mark.unit


def _rule(claim_name: str, claim_value: str, role_id: str, priority: int) -> DashboardRoleMapping:
    return DashboardRoleMapping(
        id=f"{claim_name}:{claim_value}:{priority}",
        provider="trusted_header",
        provider_key="default",
        claim_name=claim_name,
        claim_value=normalize_claim_value(claim_name, claim_value),
        role_id=role_id,
        priority=priority,
    )


# --- the matcher ---


def test_highest_priority_rule_wins_over_every_other_match() -> None:
    rules = [
        _rule("groups", "staff", "viewer-role", 1),
        _rule("groups", "platform", "operator-role", 2),
        _rule("email_domain", "example.com", "member-role", 3),
    ]
    assert match_role_id(rules, groups=("platform", "staff"), email="carol@example.com") == "member-role"
    assert match_role_id(rules[:2], groups=("platform", "staff"), email=None) == "operator-role"


def test_rules_are_evaluated_by_priority_not_by_list_order() -> None:
    rules = [_rule("groups", "staff", "viewer-role", 1), _rule("groups", "platform", "operator-role", 9)]
    assert match_role_id(list(reversed(rules)), groups=("staff", "platform")) == "operator-role"


def test_group_comparison_ignores_case_and_surrounding_space() -> None:
    rules = [_rule("groups", "  Platform  ", "operator-role", 1)]
    assert match_role_id(rules, groups=("PLATFORM",)) == "operator-role"
    assert match_role_id(rules, groups=("platform-admins",)) is None


def test_email_domain_rule_matches_the_domain_only() -> None:
    rules = [_rule("email_domain", "@Example.COM", "member-role", 1)]
    assert match_role_id(rules, email="Carol@example.com") == "member-role"
    assert match_role_id(rules, email="carol@other.example") is None
    assert match_role_id(rules, email=None) is None


def test_an_identity_without_groups_matches_no_group_rule() -> None:
    assert match_role_id([_rule("groups", "platform", "operator-role", 1)], groups=(), email="a@example.com") is None


def test_no_rules_and_unknown_claims_match_nothing() -> None:
    assert match_role_id([], groups=("platform",), email="a@example.com") is None
    unknown = _rule("department", "platform", "operator-role", 1)
    assert match_role_id([unknown], groups=("platform",), email="a@example.com") is None


def test_claim_value_normalisation_and_email_domain_helper() -> None:
    assert normalize_claim_value("email_domain", " @Example.COM ") == "example.com"
    assert normalize_claim_value("groups", " Platform ") == "platform"
    assert email_domain("Carol@Example.com") == "example.com"
    assert email_domain("not-an-email") == "not-an-email"
    assert email_domain(None) is None


# --- the groups header ---


def test_groups_header_is_split_trimmed_case_folded_and_deduplicated() -> None:
    assert parse_proxy_groups(["platform, Staff , platform"]) == ("platform", "staff")


def test_a_duplicated_groups_header_yields_no_groups() -> None:
    # Two values mean something upstream is not stripping a client-supplied copy.
    assert parse_proxy_groups(["platform", "admins"]) == ()
    assert parse_proxy_groups([]) == ()


def test_groups_header_drops_empty_and_over_long_items_and_caps_the_set() -> None:
    assert parse_proxy_groups([" , ,platform, ,"]) == ("platform",)
    too_long = "g" * (MAX_PROXY_GROUP_LENGTH + 1)
    assert parse_proxy_groups([f"{too_long},platform"]) == ("platform",)
    many = ",".join(f"g{index}" for index in range(MAX_PROXY_GROUPS + 10))
    assert len(parse_proxy_groups([many])) == MAX_PROXY_GROUPS


# --- the setting ---


def test_groups_header_defaults_to_remote_groups() -> None:
    assert Settings().dashboard_auth_proxy_groups_header == "Remote-Groups"


def test_groups_header_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER", "  X-Groups ")
    assert Settings().dashboard_auth_proxy_groups_header == "X-Groups"


@pytest.mark.parametrize("value", ["Authorization", "cookie", "X-Forwarded-For", "", "not a header"])
def test_a_dangerous_groups_header_name_is_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER", value)
    with pytest.raises(ValidationError, match="dashboard_auth_proxy_groups_header"):
        Settings()


@pytest.mark.parametrize("value", ["Remote-User", "remote-user", "REMOTE-USER"])
def test_the_groups_header_may_not_be_the_identity_header(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER", value)
    with pytest.raises(ValidationError, match="must not equal dashboard_auth_proxy_header"):
        Settings()
