"""Audit rows name who acted, how they authenticated and what they touched.

Product-path coverage for the ``attribute-audit-actor`` change: signed-in
accounts, the implicit local admin, failed sign-ins, the listing filters, and
the schema migration.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import pytest
from anyio import to_thread
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.audit.service import drain_audit_log_tasks
from app.core.auth import generate_unique_account_id
from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import AuditLog, DashboardUser, RateLimitAttempt
from app.db.session import SessionLocal
from app.modules.audit.repository import AuditLogFilters, AuditRepository

pytestmark = pytest.mark.integration

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
_PARENT_REVISION = "20260909_020000_reproject_compat_admin_credentials"
_TARGET_REVISION = "20260909_030000_add_audit_actor_columns"
_ACTOR_COLUMNS = (
    "actor_user_id",
    "actor_username",
    "actor_role_slug",
    "auth_method",
    "target_type",
    "target_id",
    "severity",
)
_INDEXES = ("idx_audit_logs_actor_user_id", "idx_audit_logs_target")


async def _rows(action: str) -> list[AuditLog]:
    assert await drain_audit_log_tasks(5.0)
    async with SessionLocal() as session:
        result = await session.execute(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        return list(result.scalars().all())


async def _setup_admin(client: AsyncClient, password: str = "password123") -> dict[str, Any]:
    response = await client.post("/api/dashboard-auth/password/setup", json={"password": password})
    assert response.status_code == 200, response.text
    return response.json()


def _auth_json(account_id: str, email: str) -> str:
    claims = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    body = base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode("ascii")
    return json.dumps(
        {
            "tokens": {
                "idToken": f"header.{body}.sig",
                "accessToken": "access-token",
                "refreshToken": "refresh-token",
                "accountId": account_id,
            }
        }
    )


async def _insert_user(username: str, *, password: str = "second-password-1", status: str = "active") -> DashboardUser:
    async with SessionLocal() as session:
        user = DashboardUser(
            id=str(uuid.uuid4()),
            username=username,
            role_id=PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR],
            status=status,
            password_hash=bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode(),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await get_dashboard_users_cache().invalidate()
    return user


@pytest.mark.asyncio
async def test_signed_in_admin_mutation_records_actor_and_target(async_client: AsyncClient) -> None:
    user_id = (await _setup_admin(async_client))["user"]["id"]
    assert (await async_client.post("/api/dashboard-auth/logout", json={})).status_code == 200
    login = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert login.status_code == 200, login.text

    assert (await async_client.put("/api/settings", json={"stickyThreadsEnabled": False})).status_code == 200
    (row,) = await _rows("settings_changed")
    assert (row.actor_user_id, row.actor_username, row.actor_role_slug, row.auth_method) == (
        user_id,
        "admin",
        "admin",
        "password",
    )
    assert (row.target_type, row.target_id, row.severity) == ("settings", "dashboard", "info")

    assert (await async_client.post("/api/dashboard-auth/guest/logout-all")).status_code == 200
    (revoked,) = await _rows("guest_sessions_revoked")
    assert revoked.actor_user_id == user_id
    assert (revoked.target_type, revoked.target_id) == ("settings", "guest_access")

    (signed_in,) = await _rows("login_success")
    assert signed_in.actor_user_id == user_id
    assert signed_in.auth_method == "password"
    assert (signed_in.target_type, signed_in.target_id) == ("user", user_id)


@pytest.mark.asyncio
async def test_account_and_api_key_routes_record_their_targets(async_client: AsyncClient) -> None:
    email, raw_account_id = "audit-actor@example.com", "acc_audit_actor"
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", _auth_json(raw_account_id, email), "application/json")},
    )
    assert imported.status_code == 200, imported.text
    account_id = generate_unique_account_id(raw_account_id, email)
    (created,) = await _rows("account_created")
    assert (created.target_type, created.target_id) == ("account", account_id)
    assert (created.actor_role_slug, created.auth_method, created.actor_user_id) == ("admin", "local_bootstrap", None)

    limited = await async_client.put(f"/api/accounts/{account_id}/usage-limit", json={"enabled": True, "percent": 80})
    assert limited.status_code == 200, limited.text
    (limit_row,) = await _rows("account_usage_limit_updated")
    assert (limit_row.target_type, limit_row.target_id) == ("account", account_id)
    assert (limit_row.actor_role_slug, limit_row.auth_method) == ("admin", "local_bootstrap")

    key = await async_client.post("/api/api-keys/", json={"name": "audit-actor-key"})
    assert key.status_code == 200, key.text
    key_id = key.json()["id"]
    (key_row,) = await _rows("api_key_created")
    assert (key_row.target_type, key_row.target_id) == ("api_key", key_id)
    assert key_row.actor_role_slug == "admin"

    listed = await async_client.get("/api/audit-logs", params={"target_type": "api_key", "target_id": key_id})
    assert listed.status_code == 200, listed.text
    (entry,) = listed.json()
    assert entry["action"] == "api_key_created"
    assert entry["target"] == {"type": "api_key", "id": key_id}
    assert entry["actor"] == {"userId": None, "username": None, "roleSlug": "admin", "authMethod": "local_bootstrap"}


@pytest.mark.asyncio
async def test_guest_login_records_the_guest_actor(async_client: AsyncClient) -> None:
    assert (await async_client.put("/api/settings", json={"guestAccessEnabled": True})).status_code == 200

    assert (await async_client.post("/api/dashboard-auth/guest/login")).status_code == 200
    (row,) = await _rows("login_success")
    assert json.loads(row.details or "{}") == {"method": "guest"}
    assert (row.actor_user_id, row.actor_username, row.actor_role_slug, row.auth_method) == (
        None,
        None,
        "guest",
        "guest",
    )
    assert row.severity == "info"


@pytest.mark.asyncio
async def test_username_required_refusals_are_audited_within_a_budget(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    await _insert_user("ops")
    assert (await async_client.post("/api/dashboard-auth/logout", json={})).status_code == 200

    for _ in range(12):
        response = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "username_required"

    rows = await _rows("login_failed")
    assert len(rows) == 8
    assert all(json.loads(row.details or "{}")["reason"] == "username_required" for row in rows)
    async with SessionLocal() as session:
        password_attempts = await session.scalar(
            select(func.count()).select_from(RateLimitAttempt).where(RateLimitAttempt.type == "password")
        )
    assert password_attempts == 0

    # The refusals spent no password budget: a real attempt is still admitted.
    still_admitted = await async_client.post(
        "/api/dashboard-auth/password/login", json={"username": "admin", "password": "password123"}
    )
    assert still_admitted.status_code == 200, still_admitted.text


@pytest.mark.asyncio
async def test_implicit_local_admin_records_bootstrap_auth_method(async_client: AsyncClient) -> None:
    assert (await async_client.put("/api/settings", json={"stickyThreadsEnabled": False})).status_code == 200

    (row,) = await _rows("settings_changed")
    assert row.actor_user_id is None
    assert row.actor_username is None
    assert row.actor_role_slug == "admin"
    assert row.auth_method == "local_bootstrap"
    assert (row.target_type, row.target_id, row.severity) == ("settings", "dashboard", "info")


@pytest.mark.asyncio
async def test_failed_sign_ins_record_reason_without_an_actor(async_client: AsyncClient) -> None:
    await _setup_admin(async_client)
    await _insert_user("ghost", status="disabled")
    assert (await async_client.post("/api/dashboard-auth/logout", json={})).status_code == 200

    attempts = [
        ({"password": "wrong-password"}, 401, "bad_password", "admin"),
        ({"username": "nobody", "password": "wrong-password"}, 401, "unknown_identity", "nobody"),
        ({"username": "ghost", "password": "second-password-1"}, 401, "disabled_user", "ghost"),
        ({"username": "has space", "password": "wrong-password"}, 401, "invalid_username", None),
    ]
    for payload, status, _, _ in attempts:
        response = await async_client.post("/api/dashboard-auth/password/login", json=payload)
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == "invalid_credentials"

    await _insert_user("ops")  # a second password holder makes the username mandatory
    ambiguous = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert ambiguous.status_code == 422
    assert ambiguous.json()["error"]["code"] == "username_required"

    rows = await _rows("login_failed")
    assert [json.loads(row.details or "{}")["reason"] for row in rows] == [
        *(reason for _, _, reason, _ in attempts),
        "username_required",
    ]
    assert [json.loads(row.details or "{}")["username"] for row in rows] == [
        *(username for _, _, _, username in attempts),
        None,
    ]
    for row in rows:
        assert row.severity == "warning"
        assert row.actor_user_id is None
        assert row.actor_username is None
        assert row.actor_role_slug is None
        assert row.auth_method is None
        assert json.loads(row.details or "{}")["method"] == "password"


@pytest.mark.asyncio
async def test_audit_log_listing_filters_and_response_shape(async_client: AsyncClient) -> None:
    base = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    async with SessionLocal() as session:
        session.add_all(
            [
                AuditLog(
                    action="login_failed",
                    actor_ip="203.0.113.1",
                    details=json.dumps({"method": "password", "username": None, "reason": "bad_password"}),
                    severity="warning",
                    timestamp=base,
                ),
                AuditLog(
                    action="login_failed",
                    actor_ip="203.0.113.2",
                    details=json.dumps({"method": "totp", "username": "alice", "reason": "bad_totp"}),
                    severity="warning",
                    timestamp=base + timedelta(hours=1),
                ),
                AuditLog(
                    action="account_updated",
                    actor_ip="203.0.113.3",
                    details=json.dumps({"account_id": "acc-1", "changed_fields": ["alias"]}),
                    actor_user_id="u-1",
                    actor_username="alice",
                    actor_role_slug="operator",
                    auth_method="password",
                    target_type="account",
                    target_id="acc-1",
                    severity="info",
                    timestamp=base + timedelta(hours=2),
                ),
                # A row written before attribution existed: every new column NULL,
                # severity from the server default.
                AuditLog(action="settings_changed", actor_ip="127.0.0.1", timestamp=base + timedelta(hours=3)),
            ]
        )
        await session.commit()

    async def _list(**params: str) -> list[dict[str, Any]]:
        response = await async_client.get("/api/audit-logs", params=params)
        assert response.status_code == 200, response.text
        return response.json()

    everything = await _list()
    assert [entry["action"] for entry in everything] == [
        "settings_changed",
        "account_updated",
        "login_failed",
        "login_failed",
    ]
    legacy, attributed, totp_failure, password_failure = everything
    assert legacy["actor"] is None
    assert legacy["target"] is None
    assert legacy["severity"] == "info"
    assert attributed["actor"] == {
        "userId": "u-1",
        "username": "alice",
        "roleSlug": "operator",
        "authMethod": "password",
    }
    assert attributed["target"] == {"type": "account", "id": "acc-1"}
    assert attributed["severity"] == "info"
    assert password_failure["severity"] == "warning"

    assert [entry["id"] for entry in await _list(actor_user_id="u-1")] == [attributed["id"]]
    assert [entry["id"] for entry in await _list(target_type="account", target_id="acc-1")] == [attributed["id"]]
    assert [entry["id"] for entry in await _list(severity="warning")] == [totp_failure["id"], password_failure["id"]]
    assert [entry["id"] for entry in await _list(reason="bad_totp")] == [totp_failure["id"]]
    assert [entry["id"] for entry in await _list(reason="bad_password")] == [password_failure["id"]]
    assert [entry["id"] for entry in await _list(action="login_failed", reason="bad_password")] == [
        password_failure["id"]
    ]
    window = await _list(
        since=(base + timedelta(hours=1)).isoformat(),
        until=(base + timedelta(hours=3)).isoformat(),
    )
    assert [entry["id"] for entry in window] == [attributed["id"], totp_failure["id"]]

    for bad in ({"reason": "Bad-Password"}, {"severity": "loud"}, {"since": "yesterday"}):
        response = await async_client.get("/api/audit-logs", params=bad)
        assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_audit_actor_columns_migration_upgrade_and_downgrade(tmp_path) -> None:
    from alembic import command

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'audit-actor.sqlite'}"

    async def _columns(engine) -> set[str]:
        async with engine.connect() as conn:
            return {row[1] for row in await conn.execute(text("PRAGMA table_info('audit_logs')"))}

    async def _indexes(engine) -> set[str]:
        async with engine.connect() as conn:
            return {row[1] for row in await conn.execute(text("PRAGMA index_list('audit_logs')"))}

    await to_thread.run_sync(lambda: run_upgrade(db_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        assert not set(_ACTOR_COLUMNS) & await _columns(engine)
        async with engine.begin() as conn:
            # The database default writes second-granular text on SQLite.
            await conn.execute(
                text("INSERT INTO audit_logs (timestamp, action) VALUES ('2026-09-09 12:00:00', 'legacy')")
            )

        await to_thread.run_sync(lambda: run_upgrade(db_url, _TARGET_REVISION, bootstrap_legacy=False))
        assert set(_ACTOR_COLUMNS) <= await _columns(engine)
        assert set(_INDEXES) <= await _indexes(engine)
        async with engine.connect() as conn:
            legacy = (
                await conn.execute(text("SELECT severity, actor_user_id, target_type, timestamp FROM audit_logs"))
            ).one()
        assert legacy == ("info", None, None, "2026-09-09 12:00:00.000000")

        # Same second in the microsecond form the ORM writes: an inclusive
        # `since` on that exact second must return both rows.
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO audit_logs (timestamp, action, severity) "
                    "VALUES ('2026-09-09 12:00:00.250000', 'fresh', 'info')"
                )
            )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            since = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
            rows = await AuditRepository(session).list_logs(AuditLogFilters(since=since))
            assert [row.action for row in rows] == ["fresh", "legacy"]
            later = AuditLogFilters(since=since + timedelta(seconds=1))
            assert await AuditRepository(session).list_logs(later) == []

        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, _PARENT_REVISION))
        assert not set(_ACTOR_COLUMNS) & await _columns(engine)
        assert not set(_INDEXES) & await _indexes(engine)
        async with engine.connect() as conn:
            surviving = await conn.execute(text("SELECT action FROM audit_logs ORDER BY id"))
            assert surviving.all() == [("legacy",), ("fresh",)]

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        assert set(_ACTOR_COLUMNS) <= await _columns(engine)
        assert set(_INDEXES) <= await _indexes(engine)
    finally:
        await engine.dispose()
