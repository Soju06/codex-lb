"""Every row of the re-evaluation table (PLAN.md 4.6) on the real request path.

A trusted-header install presents its identity on every request, so "signing
in" here is one request per cache TTL. The rules under test: a manually set
role is never touched, a provider with no rules re-evaluates nobody (D10),
``skip_role_sync`` stops re-evaluation but not provisioning, a matching rule
moves the account, no match parks it on ``no_match_role_id`` (or disables it),
the last admin is pinned instead of demoted, and a run that changes nothing
writes nothing.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.modules.dashboard_users.identity_resolver as identity_resolver
from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.auth.providers.registry import get_auth_provider_registry
from app.core.config.settings import get_settings
from app.db.models import (
    AuditLog,
    AuthProviderKind,
    DashboardAuthProvider,
    DashboardIdentity,
    DashboardRoleMapping,
    DashboardUser,
)
from app.db.session import SessionLocal
from app.modules.auth_providers.seed import auth_provider_id
from app.modules.dashboard_users.repository import DashboardUsersRepository

pytestmark = pytest.mark.integration

SESSION = "/api/dashboard-auth/session"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
MEMBER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.MEMBER]
TRUSTED_HEADER_PROVIDER_ID = auth_provider_id(AuthProviderKind.TRUSTED_HEADER)


# --- helpers ---


def _trusted_header_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_MODE", DashboardAuthMode.TRUSTED_HEADER.value)
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS", "true")
    monkeypatch.setenv("CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS", "127.0.0.1/32")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER", "Remote-User")
    monkeypatch.setenv("CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER", "Remote-Groups")
    get_settings.cache_clear()


def _as(subject: str, groups: str | None = None) -> dict[str, str]:
    headers = {"Remote-User": subject}
    if groups is not None:
        headers["Remote-Groups"] = groups
    return headers


def _error(response) -> str:
    return response.json()["error"]["code"]


async def _clear_caches() -> None:
    """What a write does in production; here it makes "the next sign-in" the next request."""

    get_auth_provider_registry().clear()
    identity_resolver.get_identity_resolution_cache().clear()
    await get_dashboard_users_cache().invalidate()


async def _add_rule(claim_value: str, role_id: str, *, priority: int = 1, claim_name: str = "groups") -> str:
    mapping_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        session.add(
            DashboardRoleMapping(
                id=mapping_id,
                provider="trusted_header",
                provider_key="default",
                claim_name=claim_name,
                claim_value=claim_value,
                role_id=role_id,
                priority=priority,
            )
        )
        await session.commit()
    await _clear_caches()
    return mapping_id


async def _set_provider(**values: Any) -> None:
    async with SessionLocal() as session:
        provider = await session.get(DashboardAuthProvider, TRUSTED_HEADER_PROVIDER_ID)
        assert provider is not None
        for key, value in values.items():
            setattr(provider, key, value)
        await session.commit()
    await _clear_caches()


async def _seed_account(username: str, subject: str, role_id: str, *, role_source: str = "manual") -> str:
    async with SessionLocal() as session:
        user = DashboardUser(id=str(uuid.uuid4()), username=username, role_id=role_id, role_source=role_source)
        session.add(user)
        await session.flush()
        session.add(
            DashboardIdentity(user_id=user.id, provider="trusted_header", provider_key="default", subject=subject)
        )
        await session.commit()
    await _clear_caches()
    return user.id


async def _user(username: str) -> DashboardUser:
    async with SessionLocal() as session:
        return (await session.execute(select(DashboardUser).where(DashboardUser.username == username))).scalar_one()


async def _identity_row(subject: str) -> DashboardIdentity:
    async with SessionLocal() as session:
        return (
            await session.execute(select(DashboardIdentity).where(DashboardIdentity.subject == subject))
        ).scalar_one()


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _stepped_up(client: AsyncClient, headers: dict[str, str]) -> None:
    """Rule edits are ``security:write``: a header account proves itself with a freshly enrolled factor."""

    start = await client.post("/api/dashboard-auth/totp/setup/start", json={}, headers=headers)
    assert start.status_code == 200, start.text
    code = pyotp.TOTP(start.json()["secret"]).now()
    confirm = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": start.json()["secret"], "code": code}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    stepped = await client.post("/api/dashboard-auth/step-up", json={"code": code}, headers=headers)
    assert stepped.status_code == 200, stepped.text


async def _sign_in(client: AsyncClient, subject: str, groups: str | None = None) -> dict[str, Any]:
    await _clear_caches()
    response = await client.get(SESSION, headers=_as(subject, groups))
    assert response.status_code == 200, response.text
    return response.json()


# --- provisioning consults the rules first ---


@pytest.mark.asyncio
async def test_a_rule_beats_the_unknown_identity_default_at_provisioning(
    async_client: AsyncClient, monkeypatch
) -> None:
    _trusted_header_mode(monkeypatch)
    await _set_provider(unknown_identity_role_id=VIEWER_ROLE)
    await _add_rule("platform", OPERATOR_ROLE)

    matched = await _sign_in(async_client, "alice@example.com", "platform, staff")
    assert matched["user"]["role"]["slug"] == "operator"
    unmatched = await _sign_in(async_client, "bob@example.com", "guests")
    assert unmatched["user"]["role"]["slug"] == "viewer"

    alice = await _user("alice.example.com")
    assert alice.role_source == "mapping"
    # The group snapshot travels with the identity row.
    assert json.loads((await _identity_row("alice@example.com")).groups_json or "[]") == ["platform", "staff"]


@pytest.mark.asyncio
async def test_an_email_domain_rule_catches_an_identity_without_groups(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _set_provider(unknown_identity_role_id=None)
    await _add_rule("example.com", VIEWER_ROLE, claim_name="email_domain")

    admitted = await _sign_in(async_client, "carol@example.com")
    assert admitted["user"]["role"]["slug"] == "viewer"
    refused = await _sign_in(async_client, "dave@other.example")
    assert refused["authenticated"] is False


@pytest.mark.asyncio
async def test_a_duplicated_groups_header_matches_no_group_rule(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _set_provider(unknown_identity_role_id=VIEWER_ROLE)
    await _add_rule("platform", OPERATOR_ROLE)

    await _clear_caches()
    response = await async_client.get(
        SESSION,
        headers=[("Remote-User", "alice@example.com"), ("Remote-Groups", "platform"), ("Remote-Groups", "platform")],
    )
    assert response.status_code == 200
    # Failing closed demotes the person instead of admitting them on a header nobody strips.
    assert response.json()["user"]["role"]["slug"] == "viewer"


# --- the re-evaluation table ---


@pytest.mark.asyncio
async def test_a_manually_set_role_survives_every_rule(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_account("ops-admin", "ops@example.com", ADMIN_ROLE, role_source="manual")
    await _add_rule("platform", VIEWER_ROLE)

    signed_in = await _sign_in(async_client, "ops@example.com", "platform")
    assert signed_in["user"]["role"]["slug"] == "admin"
    user = await _user("ops-admin")
    assert user.role_id == ADMIN_ROLE and user.role_source == "manual"
    assert await _rows("user_role_changed") == []
    assert await _rows("role_demoted_no_mapping") == []


@pytest.mark.asyncio
async def test_a_provider_with_no_rules_reevaluates_nobody(async_client: AsyncClient, monkeypatch) -> None:
    """D10: an upgraded reverse-proxy install that adds no rule keeps every account as it was."""

    _trusted_header_mode(monkeypatch)
    # Both accounts are provider-managed, which is exactly what re-evaluation would move.
    await _seed_account("ops-admin", "ops@example.com", ADMIN_ROLE, role_source="mapping")
    await _seed_account("watcher", "watcher@example.com", VIEWER_ROLE, role_source="mapping")

    assert (await _sign_in(async_client, "ops@example.com", "nothing"))["user"]["role"]["slug"] == "admin"
    assert (await _sign_in(async_client, "watcher@example.com"))["user"]["role"]["slug"] == "viewer"

    admin, watcher = await _user("ops-admin"), await _user("watcher")
    assert (admin.role_id, admin.role_source, admin.status) == (ADMIN_ROLE, "mapping", "active")
    assert (watcher.role_id, watcher.role_source, watcher.status) == (VIEWER_ROLE, "mapping", "active")
    assert await _rows("role_demoted_no_mapping") == []
    assert await _rows("user_role_changed") == []
    assert await _rows("role_source_overridden") == []


@pytest.mark.asyncio
async def test_skip_role_sync_stops_reevaluation_but_not_provisioning(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_account("watcher", "watcher@example.com", VIEWER_ROLE, role_source="mapping")
    await _add_rule("platform", OPERATOR_ROLE)
    await _set_provider(skip_role_sync=True)

    unchanged = await _sign_in(async_client, "watcher@example.com", "platform")
    assert unchanged["user"]["role"]["slug"] == "viewer"
    assert (await _user("watcher")).role_id == VIEWER_ROLE
    assert await _rows("user_role_changed") == []

    # A brand-new identity is still provisioned with the role its rule names.
    newcomer = await _sign_in(async_client, "erin@example.com", "platform")
    assert newcomer["user"]["role"]["slug"] == "operator"


@pytest.mark.asyncio
async def test_a_matching_rule_moves_a_managed_account_once(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_account("ops-admin", "ops@example.com", ADMIN_ROLE, role_source="manual")
    await _seed_account("watcher", "watcher@example.com", VIEWER_ROLE, role_source="mapping")
    await _add_rule("platform", OPERATOR_ROLE)

    before = await _user("watcher")
    promoted = await _sign_in(async_client, "watcher@example.com", "Platform")
    assert promoted["user"]["role"]["slug"] == "operator"
    after = await _user("watcher")
    assert after.role_id == OPERATOR_ROLE and after.role_source == "mapping"
    # A role change ends the account's sessions, like an administrator's edit.
    assert after.session_generation == before.session_generation + 1

    changed = await _rows("user_role_changed")
    assert len(changed) == 1
    assert '"from": "viewer"' in (changed[0].details or "") and '"to": "operator"' in (changed[0].details or "")
    assert '"source": "mapping"' in (changed[0].details or "")

    # Nothing changed, nothing written: ten more sign-ins produce no second row.
    for _ in range(10):
        assert (await _sign_in(async_client, "watcher@example.com", "platform"))["user"]["role"]["slug"] == "operator"
    assert len(await _rows("user_role_changed")) == 1
    assert (await _user("watcher")).session_generation == before.session_generation + 1


@pytest.mark.asyncio
async def test_no_matching_rule_parks_the_account_on_the_fallback_role(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_account("ops-admin", "ops@example.com", ADMIN_ROLE, role_source="manual")
    await _seed_account("boss", "boss@example.com", OPERATOR_ROLE, role_source="mapping")
    await _add_rule("platform", OPERATOR_ROLE)

    demoted = await _sign_in(async_client, "boss@example.com", "contractors")
    assert demoted["user"]["role"]["slug"] == "viewer"
    assert (await _user("boss")).role_id == VIEWER_ROLE

    rows = await _rows("role_demoted_no_mapping")
    assert len(rows) == 1 and rows[0].severity == "warning"
    assert '"from": "operator"' in (rows[0].details or "") and '"to": "viewer"' in (rows[0].details or "")
    assert '"provider": "trusted_header"' in (rows[0].details or "")

    # Already parked: the next sign-in writes nothing more.
    await _sign_in(async_client, "boss@example.com", "contractors")
    assert len(await _rows("role_demoted_no_mapping")) == 1


@pytest.mark.asyncio
async def test_no_match_without_a_fallback_disables_the_account(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_account("ops-admin", "ops@example.com", ADMIN_ROLE, role_source="manual")
    await _seed_account("watcher", "watcher@example.com", VIEWER_ROLE, role_source="mapping")
    await _set_provider(no_match_role_id=None)
    await _add_rule("platform", OPERATOR_ROLE)

    await _clear_caches()
    refused = await async_client.get("/api/settings", headers=_as("watcher@example.com", "contractors"))
    assert refused.status_code == 401 and _error(refused) == "account_disabled"
    assert (await _user("watcher")).status == "disabled"

    rows = await _rows("role_demoted_no_mapping")
    assert len(rows) == 1 and '"to": null' in (rows[0].details or "")


@pytest.mark.asyncio
async def test_reevaluation_never_removes_the_last_admin(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    alice = await _sign_in(async_client, "alice@example.com")
    assert alice["user"]["role"]["slug"] == "admin"
    assert (await _user("alice.example.com")).role_source == "mapping"
    await _add_rule("platform", OPERATOR_ROLE)

    pinned = await _sign_in(async_client, "alice@example.com", "contractors")
    assert pinned["user"]["role"]["slug"] == "admin"
    user = await _user("alice.example.com")
    assert user.role_id == ADMIN_ROLE and user.role_source == "manual" and user.status == "active"

    rows = await _rows("role_source_overridden")
    assert len(rows) == 1 and rows[0].severity == "warning"
    assert '"reason": "last_admin_protected"' in (rows[0].details or "")
    assert await _rows("role_demoted_no_mapping") == []

    # Stable, not retried: a later sign-in repeats neither the change nor the row.
    await _sign_in(async_client, "alice@example.com", "contractors")
    assert len(await _rows("role_source_overridden")) == 1


@pytest.mark.asyncio
async def test_the_last_admin_check_is_read_under_the_accounts_write_intent(
    async_client: AsyncClient, monkeypatch
) -> None:
    """The invariant is read behind the same serialisation the admin path uses.

    Read before locking, two replicas demoting two mapping-managed admins at
    the same moment would each see the other survive and both commit, leaving
    an install with no admin and no way back in.
    """

    _trusted_header_mode(monkeypatch)
    calls: list[str] = []
    acquire = DashboardUsersRepository.acquire_write_intent
    count = DashboardUsersRepository.count_active_admins

    async def record_intent(self: DashboardUsersRepository) -> None:
        calls.append("write_intent")
        await acquire(self)

    async def record_count(self: DashboardUsersRepository, *, exclude_user_id: str | None = None) -> int:
        calls.append("count_active_admins")
        return await count(self, exclude_user_id=exclude_user_id)

    monkeypatch.setattr(DashboardUsersRepository, "acquire_write_intent", record_intent)
    monkeypatch.setattr(DashboardUsersRepository, "count_active_admins", record_count)

    assert (await _sign_in(async_client, "alice@example.com"))["user"]["role"]["slug"] == "admin"
    await _add_rule("platform", OPERATOR_ROLE)
    calls.clear()

    pinned = await _sign_in(async_client, "alice@example.com", "contractors")
    assert pinned["user"]["role"]["slug"] == "admin"
    assert (await _user("alice.example.com")).role_source == "manual"
    # Never counted before the lock, and counted once it is held.
    assert calls == ["write_intent", "count_active_admins"]


@pytest.mark.asyncio
async def test_changing_the_unknown_identity_default_is_not_retroactive(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _add_rule("platform", ADMIN_ROLE)
    assert (await _sign_in(async_client, "alice@example.com", "platform"))["user"]["role"]["slug"] == "admin"

    await _set_provider(unknown_identity_role_id=VIEWER_ROLE)
    kept = await _sign_in(async_client, "alice@example.com", "platform")
    assert kept["user"]["role"]["slug"] == "admin"
    assert (await _user("alice.example.com")).role_id == ADMIN_ROLE
    assert await _rows("role_demoted_no_mapping") == []


@pytest.mark.asyncio
async def test_the_highest_priority_rule_decides_on_the_request_path(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    await _seed_account("ops-admin", "ops@example.com", ADMIN_ROLE, role_source="manual")
    await _seed_account("watcher", "watcher@example.com", VIEWER_ROLE, role_source="mapping")
    await _add_rule("staff", MEMBER_ROLE, priority=1)
    await _add_rule("platform", OPERATOR_ROLE, priority=2)

    signed_in = await _sign_in(async_client, "watcher@example.com", "staff,platform")
    assert signed_in["user"]["role"]["slug"] == "operator"


@pytest.mark.asyncio
async def test_a_new_rule_admits_an_identity_that_was_refused(async_client: AsyncClient, monkeypatch) -> None:
    """The write itself must clear the cached refusal, or the operator sees a rule that does nothing."""

    _trusted_header_mode(monkeypatch)
    admin = _as("alice@example.com")
    assert (await async_client.get(SESSION, headers=admin)).json()["user"]["role"]["slug"] == "admin"
    await _stepped_up(async_client, admin)
    await _set_provider(unknown_identity_role_id=None)

    refused = await async_client.get(SESSION, headers=_as("frank@example.com", "platform"))
    assert refused.json()["authenticated"] is False

    created = await async_client.post(
        "/api/role-mappings",
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "platform",
            "roleId": VIEWER_ROLE,
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text

    # No cache clearing here on purpose: the rule write did it.
    admitted = await async_client.get(SESSION, headers=_as("frank@example.com", "platform"))
    assert admitted.json()["authenticated"] is True
    assert admitted.json()["user"]["role"]["slug"] == "viewer"
