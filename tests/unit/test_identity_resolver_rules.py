"""Pure rules of the identity resolver and the provider registry (no database)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.providers import TrustedHeaderProvider
from app.core.auth.providers.registry import provider_active
from app.db.models import AuthProviderKind, DashboardAuthProvider
from app.modules.dashboard_users.identity_resolver import jit_username_candidates, slugify_subject
from app.modules.scim.service import SLUG_PREFIX

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("alice", "alice"),
        ("Alice.Smith", "alice.smith"),
        ("alice@example.com", "alice.example.com"),
        ("ALICE@EXAMPLE.COM", "alice.example.com"),
        ("al ice!#$%", "alice"),
        ("first_last-01", "first_last-01"),
        ("a" * 80, "a" * 56),
        ("ünï", "n"),
    ],
)
def test_slugify_subject_rules(subject: str, expected: str) -> None:
    assert slugify_subject(subject) == expected


def test_slugify_subject_falls_back_to_hash_when_empty() -> None:
    slug = slugify_subject("!!!")
    assert slug.startswith("th-") and len(slug) == 11
    assert slug == slugify_subject("!!!")
    assert slug != slugify_subject("???")


def test_the_provisioning_path_shares_the_rules_and_takes_its_own_empty_prefix() -> None:
    """One copy of the slug rules serves both paths (spec: Just-in-time usernames).

    The only difference is the marker a value that slugifies to nothing falls
    back to, so a name says which path made it and no account provisioned
    before SCIM existed changes its name.
    """

    for value in ("Alice.Smith", "alice@example.com", "al ice!", "admin"):
        assert slugify_subject(value, empty_prefix=SLUG_PREFIX) == slugify_subject(value)

    from_scim = slugify_subject("!!!", empty_prefix=SLUG_PREFIX)
    assert from_scim.startswith(SLUG_PREFIX)
    assert from_scim.removeprefix(SLUG_PREFIX) == slugify_subject("!!!").removeprefix("th-")


def test_jit_username_candidates_number_collisions() -> None:
    it = jit_username_candidates("alice")
    assert [next(it) for _ in range(4)] == ["alice", "alice-2", "alice-3", "alice-4"]


def test_trusted_header_identity_casefolds_subject_and_parses_email() -> None:
    identity = TrustedHeaderProvider().identity_from_subject("  Alice@Example.COM ")
    assert identity is not None
    assert identity.provider == "trusted_header"
    assert identity.provider_key == "default"
    assert identity.subject == "alice@example.com"
    assert identity.email == "alice@example.com"
    assert identity.display_name == "Alice@Example.COM"
    assert identity.groups == ()

    plain = TrustedHeaderProvider().identity_from_subject("ops-team")
    assert plain is not None
    assert plain.subject == "ops-team"
    assert plain.email is None


def _row(kind: str, *, enabled: bool = True) -> DashboardAuthProvider:
    now = datetime.now(UTC)
    return DashboardAuthProvider(
        id=f"prov-{kind}",
        kind=kind,
        provider_key="default",
        enabled=enabled,
        label=kind,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    ("kind", "enabled", "mode", "expected"),
    [
        ("password", True, DashboardAuthMode.STANDARD, True),
        ("password", True, DashboardAuthMode.TRUSTED_HEADER, True),
        ("password", False, DashboardAuthMode.STANDARD, False),
        ("trusted_header", True, DashboardAuthMode.STANDARD, False),
        ("trusted_header", True, DashboardAuthMode.TRUSTED_HEADER, True),
        ("trusted_header", False, DashboardAuthMode.TRUSTED_HEADER, False),
        # OIDC serves both modes that have a dashboard sign-in, and never the
        # one that has turned dashboard authentication off: an install that
        # bypasses auth must not grow a flow that mints sessions.
        ("oidc", True, DashboardAuthMode.STANDARD, True),
        ("oidc", True, DashboardAuthMode.TRUSTED_HEADER, True),
        ("oidc", True, DashboardAuthMode.DISABLED, False),
        ("oidc", False, DashboardAuthMode.STANDARD, False),
        # A kind with no implementation is never active.
        ("saml", True, DashboardAuthMode.STANDARD, False),
    ],
)
def test_provider_active_follows_row_and_mode(
    kind: str, enabled: bool, mode: DashboardAuthMode, expected: bool
) -> None:
    assert provider_active(_row(kind, enabled=enabled), mode) is expected


def test_provider_kinds_are_the_wire_values() -> None:
    assert {kind.value for kind in AuthProviderKind} == {"password", "trusted_header", "oidc"}
