"""Product-path coverage for the group-to-role rules API (PR-2c-2).

The rules an operator edits, the order the resolver reads, and the refusals
that keep the list well-formed: one rule per value, a server-owned contiguous
order, and no rule handing out a role the caller could not hand out itself.
"""

from __future__ import annotations

import uuid
from typing import Any

import pyotp
import pytest
from alembic import command
from anyio import to_thread
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import (
    AuditLog,
    AuthProviderKind,
    DashboardIdentity,
    DashboardRoleGrant,
    DashboardRoleMapping,
    DashboardRoleRecord,
    DashboardUser,
)
from app.db.session import SessionLocal
from app.modules.auth_providers.seed import auth_provider_id
from app.modules.role_mappings.repository import MAX_MAPPINGS_PER_PROVIDER, RoleMappingsRepository

pytestmark = pytest.mark.integration

MAPPINGS = "/api/role-mappings"
SESSION = "/api/dashboard-auth/session"
ADMIN_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
OPERATOR_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR]
VIEWER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]
MEMBER_ROLE = PRESET_ROLE_IDS[PresetRoleSlug.MEMBER]
TRUSTED_HEADER_PROVIDER_ID = auth_provider_id(AuthProviderKind.TRUSTED_HEADER)

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
_PARENT_REVISION = "20260910_010000_add_dashboard_auth_providers"
_TARGET_REVISION = "20260910_020000_add_dashboard_role_mappings"


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


async def _stepped_up(client: AsyncClient, headers: dict[str, str]) -> None:
    """Rules are ``security:write``, so every edit needs a credential re-verified just now."""

    start = await client.post("/api/dashboard-auth/totp/setup/start", json={}, headers=headers)
    assert start.status_code == 200, start.text
    code = pyotp.TOTP(start.json()["secret"]).now()
    confirm = await client.post(
        "/api/dashboard-auth/totp/setup/confirm", json={"secret": start.json()["secret"], "code": code}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    stepped = await client.post("/api/dashboard-auth/step-up", json={"code": code}, headers=headers)
    assert stepped.status_code == 200, stepped.text


async def _admin(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    _trusted_header_mode(monkeypatch)
    headers = _as("alice@example.com")
    assert (await client.get(SESSION, headers=headers)).json()["user"]["role"]["slug"] == "admin"
    await _stepped_up(client, headers)
    return headers


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def _create(client: AsyncClient, headers: dict[str, str], **payload: Any) -> dict[str, Any]:
    body = {
        "provider": "trusted_header",
        "providerKey": "default",
        "claimName": "groups",
        "roleId": VIEWER_ROLE,
        **payload,
    }
    response = await client.post(MAPPINGS, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def _priorities() -> list[tuple[str, int]]:
    async with SessionLocal() as session:
        rows = (
            await session.execute(select(DashboardRoleMapping).order_by(DashboardRoleMapping.priority.desc()))
        ).scalars()
        return [(row.claim_value, row.priority) for row in rows]


# --- CRUD ---


@pytest.mark.asyncio
async def test_rules_are_created_ordered_and_audited(async_client: AsyncClient, monkeypatch) -> None:
    headers = await _admin(async_client, monkeypatch)

    assert (await async_client.get(MAPPINGS, headers=headers)).json() == []

    first = await _create(async_client, headers, claimValue="staff", roleId=VIEWER_ROLE)
    assert first["priority"] == 1 and first["claimValue"] == "staff" and first["roleId"] == VIEWER_ROLE
    second = await _create(async_client, headers, claimValue="  Platform ", roleId=OPERATOR_ROLE)
    # Values are stored normalised, and a new rule appends at the bottom.
    assert second["claimValue"] == "platform" and second["priority"] == 1
    assert await _priorities() == [("staff", 2), ("platform", 1)]

    listed = await async_client.get(MAPPINGS, headers=headers)
    assert [row["claimValue"] for row in listed.json()] == ["staff", "platform"]
    filtered = await async_client.get(f"{MAPPINGS}?provider=oidc", headers=headers)
    assert filtered.json() == []

    audit = await _rows("role_mapping_changed")
    assert len(audit) == 2
    assert audit[0].target_type == "role_mapping" and audit[0].target_id == first["id"]
    assert '"operation": "created"' in (audit[0].details or "")
    assert '"role": "viewer"' in (audit[0].details or "")
    assert audit[0].actor_username == "alice.example.com"


@pytest.mark.asyncio
async def test_a_rule_is_edited_and_deleted_without_leaving_a_gap(async_client: AsyncClient, monkeypatch) -> None:
    headers = await _admin(async_client, monkeypatch)
    top = await _create(async_client, headers, claimValue="staff")
    middle = await _create(async_client, headers, claimValue="platform")
    await _create(async_client, headers, claimValue="contractors")
    assert await _priorities() == [("staff", 3), ("platform", 2), ("contractors", 1)]

    edited = await async_client.patch(
        f"{MAPPINGS}/{middle['id']}", json={"roleId": OPERATOR_ROLE, "claimValue": "Platform-Team"}, headers=headers
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["roleId"] == OPERATOR_ROLE and edited.json()["claimValue"] == "platform-team"
    assert edited.json()["priority"] == 2

    # Editing only the value still records which role the rule hands out.
    revalued = await async_client.patch(f"{MAPPINGS}/{middle['id']}", json={"claimValue": "platform"}, headers=headers)
    assert revalued.status_code == 200, revalued.text

    deleted = await async_client.delete(f"{MAPPINGS}/{top['id']}", headers=headers)
    assert deleted.status_code == 204
    assert await _priorities() == [("platform", 2), ("contractors", 1)]

    audit = {row.target_id: (row.details or "") for row in await _rows("role_mapping_changed")}
    assert '"operation": "updated"' in audit[middle["id"]] and '"role": "operator"' in audit[middle["id"]]
    assert '"operation": "deleted"' in audit[top["id"]] and '"role": "viewer"' in audit[top["id"]]

    gone = await async_client.patch(f"{MAPPINGS}/{top['id']}", json={"claimValue": "x"}, headers=headers)
    assert gone.status_code == 404 and _error(gone) == "mapping_not_found"


@pytest.mark.asyncio
async def test_reordering_writes_the_whole_order_in_one_go(async_client: AsyncClient, monkeypatch) -> None:
    headers = await _admin(async_client, monkeypatch)
    rules = [await _create(async_client, headers, claimValue=value) for value in ("a", "b", "c", "d")]
    assert await _priorities() == [("a", 4), ("b", 3), ("c", 2), ("d", 1)]

    moved_to_top = [rules[3]["id"], *(rule["id"] for rule in rules[:3])]
    order = {"provider": "trusted_header", "providerKey": "default", "ids": moved_to_top}
    reordered = await async_client.put(f"{MAPPINGS}/order", json=order, headers=headers)
    assert reordered.status_code == 200, reordered.text
    assert [row["claimValue"] for row in reordered.json()] == ["d", "a", "b", "c"]
    assert await _priorities() == [("d", 4), ("a", 3), ("b", 2), ("c", 1)]

    stale = await async_client.put(
        f"{MAPPINGS}/order",
        json={"provider": "trusted_header", "providerKey": "default", "ids": [rules[0]["id"]]},
        headers=headers,
    )
    assert stale.status_code == 409 and _error(stale) == "order_stale"
    duplicated = await async_client.put(
        f"{MAPPINGS}/order",
        json={"provider": "trusted_header", "providerKey": "default", "ids": [rules[0]["id"]] * 4},
        headers=headers,
    )
    assert duplicated.status_code == 409 and _error(duplicated) == "order_stale"
    assert await _priorities() == [("d", 4), ("a", 3), ("b", 2), ("c", 1)]


# --- refusals ---


@pytest.mark.asyncio
async def test_the_rule_list_refuses_duplicates_unknown_claims_and_unknown_providers(
    async_client: AsyncClient, monkeypatch
) -> None:
    headers = await _admin(async_client, monkeypatch)
    await _create(async_client, headers, claimValue="platform")

    duplicate = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "PLATFORM",
            "roleId": OPERATOR_ROLE,
        },
        headers=headers,
    )
    assert duplicate.status_code == 409 and _error(duplicate) == "mapping_exists"

    unknown_claim = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "department",
            "claimValue": "platform",
            "roleId": VIEWER_ROLE,
        },
        headers=headers,
    )
    assert unknown_claim.status_code == 422 and _error(unknown_claim) == "unknown_claim"

    unknown_provider = await async_client.post(
        MAPPINGS,
        json={
            "provider": "oidc",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "platform",
            "roleId": VIEWER_ROLE,
        },
        headers=headers,
    )
    assert unknown_provider.status_code == 404 and _error(unknown_provider) == "provider_not_found"

    not_assignable = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "everyone",
            "roleId": MEMBER_ROLE,
        },
        headers=headers,
    )
    assert not_assignable.status_code == 422 and _error(not_assignable) == "role_not_assignable"
    assert len(await _priorities()) == 1


@pytest.mark.asyncio
async def test_the_rule_list_is_bounded(async_client: AsyncClient, monkeypatch) -> None:
    headers = await _admin(async_client, monkeypatch)
    async with SessionLocal() as session:
        session.add_all(
            DashboardRoleMapping(
                id=str(uuid.uuid4()),
                provider="trusted_header",
                provider_key="default",
                claim_name="groups",
                claim_value=f"group-{index}",
                role_id=VIEWER_ROLE,
                priority=index + 1,
            )
            for index in range(MAX_MAPPINGS_PER_PROVIDER)
        )
        await session.commit()

    refused = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "one-too-many",
            "roleId": VIEWER_ROLE,
        },
        headers=headers,
    )
    assert refused.status_code == 409 and _error(refused) == "mapping_limit_reached"


@pytest.mark.asyncio
async def test_editing_rules_needs_an_attributable_account_and_a_fresh_step_up(
    async_client: AsyncClient, monkeypatch
) -> None:
    # No account behind the request at all (implicit local admin): reads too.
    for path in (MAPPINGS, f"{MAPPINGS}/assignable-roles"):
        listed = await async_client.get(path)
        assert listed.status_code == 409 and _error(listed) == "admin_account_required"

    refused = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "platform",
            "roleId": VIEWER_ROLE,
        },
    )
    assert refused.status_code == 409 and _error(refused) == "admin_account_required"

    _trusted_header_mode(monkeypatch)
    headers = _as("alice@example.com")
    assert (await async_client.get(SESSION, headers=headers)).json()["user"]["role"]["slug"] == "admin"
    stale = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "platform",
            "roleId": VIEWER_ROLE,
        },
        headers=headers,
    )
    # A header account holds no credential yet, so it cannot step up at all.
    assert stale.status_code == 403 and _error(stale) in {"step_up_required", "step_up_unavailable"}
    assert await _priorities() == []


@pytest.mark.asyncio
async def test_a_rule_may_not_hand_out_more_than_the_caller_holds(async_client: AsyncClient, monkeypatch) -> None:
    _trusted_header_mode(monkeypatch)
    role_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        session.add(DashboardRoleRecord(id=role_id, slug="sec", name="Sec", kind="custom"))
        session.add_all(
            DashboardRoleGrant(role_id=role_id, permission=permission, scope="all")
            for permission in ("dashboard:read", "accounts:read", "ops:write", "security:write")
        )
        user = DashboardUser(id=str(uuid.uuid4()), username="sec-officer", role_id=role_id, role_source="manual")
        session.add(user)
        await session.flush()
        session.add(
            DashboardIdentity(
                user_id=user.id, provider="trusted_header", provider_key="default", subject="sec@example.com"
            )
        )
        await session.commit()
    headers = _as("sec@example.com")
    await _stepped_up(async_client, headers)

    too_wide = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "platform",
            "roleId": ADMIN_ROLE,
        },
        headers=headers,
    )
    assert too_wide.status_code == 403 and _error(too_wide) == "insufficient_delegation"
    assert await _priorities() == []

    within = await _create(async_client, headers, claimValue="platform", roleId=VIEWER_ROLE)
    assert within["roleId"] == VIEWER_ROLE


# --- delegation on rules that already exist ---


async def _sec_officer(async_client: AsyncClient, monkeypatch) -> dict[str, str]:
    """A custom role that may edit sign-in rules but may not hand out admin."""

    _trusted_header_mode(monkeypatch)
    role_id = str(uuid.uuid4())
    async with SessionLocal() as session:
        session.add(DashboardRoleRecord(id=role_id, slug="sec", name="Sec", kind="custom"))
        session.add_all(
            DashboardRoleGrant(role_id=role_id, permission=permission, scope="all")
            for permission in ("dashboard:read", "accounts:read", "ops:write", "security:write")
        )
        user = DashboardUser(id=str(uuid.uuid4()), username="sec-officer", role_id=role_id, role_source="manual")
        session.add(user)
        await session.flush()
        session.add(
            DashboardIdentity(
                user_id=user.id, provider="trusted_header", provider_key="default", subject="sec@example.com"
            )
        )
        await session.commit()
    headers = _as("sec@example.com")
    await _stepped_up(async_client, headers)
    return headers


@pytest.mark.asyncio
async def test_a_rule_that_hands_out_more_than_the_caller_holds_cannot_be_edited_either(
    async_client: AsyncClient, monkeypatch
) -> None:
    """Retargeting, deleting or promoting a rule is the same delegation as writing it."""

    admin = await _admin(async_client, monkeypatch)
    admins_only = await _create(async_client, admin, claimValue="platform", roleId=ADMIN_ROLE)
    watchers = await _create(async_client, admin, claimValue="staff", roleId=VIEWER_ROLE)
    headers = await _sec_officer(async_client, monkeypatch)

    # The value is the whole rule: pointing it at a group the caller is in is a promotion.
    retargeted = await async_client.patch(
        f"{MAPPINGS}/{admins_only['id']}", json={"claimValue": "sec-team"}, headers=headers
    )
    assert retargeted.status_code == 403 and _error(retargeted) == "insufficient_delegation"

    lowered = await async_client.patch(f"{MAPPINGS}/{admins_only['id']}", json={"roleId": VIEWER_ROLE}, headers=headers)
    assert lowered.status_code == 403 and _error(lowered) == "insufficient_delegation"

    removed = await async_client.delete(f"{MAPPINGS}/{admins_only['id']}", headers=headers)
    assert removed.status_code == 403 and _error(removed) == "insufficient_delegation"

    promoted = await async_client.put(
        f"{MAPPINGS}/order",
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "ids": [watchers["id"], admins_only["id"]],
        },
        headers=headers,
    )
    assert promoted.status_code == 403 and _error(promoted) == "insufficient_delegation"

    # Nothing moved, and the rule still hands out what it did.
    assert await _priorities() == [("platform", 2), ("staff", 1)]
    async with SessionLocal() as session:
        untouched = await session.get(DashboardRoleMapping, admins_only["id"])
    assert untouched is not None and untouched.role_id == ADMIN_ROLE

    # Its own rules stay editable, and an order that moves nothing else is fine.
    edited = await async_client.patch(f"{MAPPINGS}/{watchers['id']}", json={"claimValue": "everyone"}, headers=headers)
    assert edited.status_code == 200, edited.text
    unchanged = await async_client.put(
        f"{MAPPINGS}/order",
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "ids": [admins_only["id"], watchers["id"]],
        },
        headers=headers,
    )
    assert unchanged.status_code == 200, unchanged.text


@pytest.mark.asyncio
async def test_a_rule_whose_role_is_no_longer_assignable_still_refuses_the_wrong_caller(
    async_client: AsyncClient, monkeypatch
) -> None:
    """A rule the caller may not hand out refuses as a permission problem, not as a broken rule."""

    admin = await _admin(async_client, monkeypatch)
    admins_only = await _create(async_client, admin, claimValue="platform", roleId=ADMIN_ROLE)
    async with SessionLocal() as session:
        role = await session.get(DashboardRoleRecord, ADMIN_ROLE)
        assert role is not None
        role.assignable_to_users = False
        await session.commit()

    headers = await _sec_officer(async_client, monkeypatch)
    refused = await async_client.delete(f"{MAPPINGS}/{admins_only['id']}", headers=headers)
    assert refused.status_code == 403 and _error(refused) == "insufficient_delegation"


# --- the roles a caller may hand out ---


@pytest.mark.asyncio
async def test_the_rules_card_can_name_the_roles_its_caller_may_hand_out(
    async_client: AsyncClient, monkeypatch
) -> None:
    """``users:manage`` is a different permission, so the rules surface serves its own list."""

    admin = await _admin(async_client, monkeypatch)
    listed = await async_client.get(f"{MAPPINGS}/assignable-roles", headers=admin)
    assert listed.status_code == 200, listed.text
    assert [role["slug"] for role in listed.json()] == ["admin", "operator", "viewer"]
    assert listed.json()[0]["name"] == "Admin" and listed.json()[0]["locked"] is True

    headers = await _sec_officer(async_client, monkeypatch)
    offered = await async_client.get(f"{MAPPINGS}/assignable-roles", headers=headers)
    assert offered.status_code == 200, offered.text
    # Only what it could hand out itself: never admin, never operator.
    assert [role["slug"] for role in offered.json()] == ["viewer", "sec"]
    assert offered.json()[1]["locked"] is False


# --- concurrent writers ---


@pytest.mark.asyncio
async def test_a_write_racing_another_writer_asks_the_caller_to_reload(async_client: AsyncClient, monkeypatch) -> None:
    """A renumber from a stale snapshot collides on the priority; that is a reload, not a 500."""

    headers = await _admin(async_client, monkeypatch)
    await _create(async_client, headers, claimValue="staff")
    # A write clears the identity cache, so warm it again first: the resolver
    # reads the same rules, and this test only wants to stale the writer's read.
    assert (await async_client.get(MAPPINGS, headers=headers)).status_code == 200

    original = RoleMappingsRepository.list_for_provider
    stale = {"armed": True}

    async def stale_snapshot(self, provider: str, provider_key: str):
        # What a second replica leaves behind: the rows this write plans around
        # are already gone by the time it renumbers.
        if stale["armed"]:
            stale["armed"] = False
            return []
        return await original(self, provider, provider_key)

    monkeypatch.setattr(RoleMappingsRepository, "list_for_provider", stale_snapshot)
    collided = await async_client.post(
        MAPPINGS,
        json={
            "provider": "trusted_header",
            "providerKey": "default",
            "claimName": "groups",
            "claimValue": "platform",
            "roleId": VIEWER_ROLE,
        },
        headers=headers,
    )
    assert collided.status_code == 409 and _error(collided) == "order_stale"
    # The refused write left nothing behind.
    assert await _priorities() == [("staff", 1)]


# --- the access summary ---


@pytest.mark.asyncio
async def test_the_first_rule_shows_up_in_the_access_summary(async_client: AsyncClient, monkeypatch) -> None:
    headers = await _admin(async_client, monkeypatch)
    assert (await async_client.get(SESSION, headers=headers)).json()["accessSummary"]["roleMappings"] == 0
    await _create(async_client, headers, claimValue="platform")
    assert (await async_client.get(SESSION, headers=headers)).json()["accessSummary"]["roleMappings"] == 1


# --- schema ---


@pytest.mark.asyncio
async def test_role_mappings_migration_upgrades_and_downgrades(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'mappings.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        config = _build_alembic_config(db_url)
        # Re-running the upgrade body is a no-op (table and index guards).
        await to_thread.run_sync(lambda: command.stamp(config, _PARENT_REVISION))
        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        async with engine.connect() as conn:
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_role_mappings')"))}
            rows = list(await conn.execute(text("SELECT COUNT(*) FROM dashboard_role_mappings")))
            indexes = {row[1] for row in await conn.execute(text("PRAGMA index_list('dashboard_role_mappings')"))}
            schema = list(
                await conn.execute(text("SELECT sql FROM sqlite_master WHERE name = 'dashboard_role_mappings'"))
            )[0][0]
        assert columns == {
            "id",
            "provider",
            "provider_key",
            "claim_name",
            "claim_value",
            "role_id",
            "priority",
            "created_at",
            "updated_at",
        }
        # The table arrives empty: an upgrade changes nobody's role.
        assert rows[0][0] == 0
        assert "idx_dashboard_role_mappings_provider" in indexes
        assert "uq_dashboard_role_mappings_priority" in schema
        assert "ON DELETE RESTRICT" in schema

        await to_thread.run_sync(lambda: command.downgrade(config, _PARENT_REVISION))
        async with engine.connect() as conn:
            tables = {row[0] for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "dashboard_role_mappings" not in tables
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        # A final walk to head proves the single-head graph. Deliberately not
        # asserted equal to ``_TARGET_REVISION``: this revision is no longer the
        # newest one, and pinning that would break on every later migration.
        assert result.current_revision == _HEAD_REVISION
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_two_rules_of_one_provider_cannot_share_a_priority() -> None:
    from sqlalchemy.exc import IntegrityError

    async with SessionLocal() as session:
        session.add_all(
            DashboardRoleMapping(
                id=str(uuid.uuid4()),
                provider="trusted_header",
                provider_key="default",
                claim_name="groups",
                claim_value=value,
                role_id=VIEWER_ROLE,
                priority=1,
            )
            for value in ("a", "b")
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
