"""Schema, backfill, and compat-projection tests for the per-user account tables.

Release N keeps the legacy shared-admin columns on ``dashboard_settings`` as a
projection of the ``admin`` user row. These tests prove (a) the migration
creates the tables and backfills the existing credential, (b) every legacy
credential write reaches the user row, and (c) the TOTP replay counter cannot
be advanced on one side only.
"""

from __future__ import annotations

import uuid

import pytest
from anyio import to_thread
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.config.settings import get_settings
from app.core.exceptions import DashboardSettingsConflictError
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import ApiKey, DashboardIdentity, DashboardSettings, DashboardUser
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_users.compat import COMPAT_ADMIN_USER_ID, COMPAT_ADMIN_USERNAME
from app.modules.settings.repository import SettingsRepository

pytestmark = pytest.mark.integration

_HEAD_REVISION = inspect_migration_state(get_settings().database_url).head_revision
PARENT_REVISION = "20260909_000000_add_dashboard_roles"
TARGET_REVISION = "20260909_010000_add_dashboard_users"


async def _compat_user() -> DashboardUser | None:
    async with SessionLocal() as session:
        return (
            await session.execute(select(DashboardUser).where(DashboardUser.username == COMPAT_ADMIN_USERNAME))
        ).scalar_one_or_none()


async def _legacy_settings() -> DashboardSettings:
    async with SessionLocal() as session:
        return (await session.execute(select(DashboardSettings))).scalar_one()


@pytest.mark.asyncio
async def test_first_run_password_setup_creates_the_admin_user(async_client) -> None:
    assert await _compat_user() is None
    setup = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert setup.status_code == 200, setup.text

    user = await _compat_user()
    assert user is not None
    assert user.id == COMPAT_ADMIN_USER_ID
    assert user.role_id == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
    assert user.status == "active"
    assert user.role_source == "manual"
    assert user.is_break_glass is True
    assert user.password_hash == (await _legacy_settings()).password_hash

    # A second setup attempt is refused and leaves exactly one admin row.
    again = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password456"})
    assert again.status_code == 409
    async with SessionLocal() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM dashboard_users"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_password_change_and_removal_are_mirrored(async_client) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    login = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert login.status_code == 200

    changed = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "password456"},
    )
    assert changed.status_code == 200, changed.text
    user = await _compat_user()
    legacy = await _legacy_settings()
    assert user is not None and user.password_hash == legacy.password_hash

    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password456"})
    assert removed.status_code == 200, removed.text
    user = await _compat_user()
    assert user is not None
    assert user.password_hash is None
    assert user.totp_secret_encrypted is None
    assert (await _legacy_settings()).password_hash is None

    # Setting a password again re-arms the surviving admin row instead of
    # leaving it credential-less next to a populated legacy column.
    again = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password789"})
    assert again.status_code == 200, again.text
    user = await _compat_user()
    legacy = await _legacy_settings()
    assert user is not None and user.id == COMPAT_ADMIN_USER_ID
    assert user.password_hash is not None and user.password_hash == legacy.password_hash
    async with SessionLocal() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM dashboard_users"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_mirrored_writes_create_the_admin_row_when_an_old_replica_left_only_the_legacy_column(db_setup) -> None:
    # A replica of the previous release configured the password: legacy column
    # set, no user row. The next mirrored write must self-heal the projection.
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        await repository.get_settings()
        await session.execute(text("UPDATE dashboard_settings SET password_hash = '$2b$old-replica' WHERE id = 1"))
        await session.commit()
    assert await _compat_user() is None

    async with SessionLocal() as session:
        await DashboardAuthRepository(session).set_totp_secret(b"secret-from-new-replica")
    user = await _compat_user()
    assert user is not None and user.id == COMPAT_ADMIN_USER_ID
    assert user.password_hash == "$2b$old-replica"
    assert user.totp_secret_encrypted == b"secret-from-new-replica"

    async with SessionLocal() as session:
        user_row = (await session.execute(select(DashboardUser))).scalar_one()
        await session.delete(user_row)
        await session.commit()
    async with SessionLocal() as session:
        await DashboardAuthRepository(session).set_password_hash("$2b$new-replica")
    user = await _compat_user()
    assert user is not None and user.password_hash == "$2b$new-replica"


@pytest.mark.asyncio
async def test_mirror_is_reapplied_when_the_settings_commit_conflicts(async_client, monkeypatch) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200

    original_commit_refresh = SettingsRepository.commit_refresh
    attempts = {"count": 0}

    async def _conflict_once(self, settings, *, on_committed=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            # A concurrent settings writer won the optimistic version race:
            # commit_refresh rolls the whole transaction back, including the
            # flushed compat-admin mirror.
            await self._session.rollback()
            raise DashboardSettingsConflictError()
        await original_commit_refresh(self, settings, on_committed=on_committed)

    monkeypatch.setattr(SettingsRepository, "commit_refresh", _conflict_once)
    async with SessionLocal() as session:
        await DashboardAuthRepository(session).set_password_hash("$2b$retried")
    assert attempts["count"] == 2

    user = await _compat_user()
    legacy = await _legacy_settings()
    assert legacy.password_hash == "$2b$retried"
    assert user is not None and user.password_hash == "$2b$retried"


@pytest.mark.asyncio
async def test_totp_secret_and_replay_counter_are_mirrored(async_client) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    assert (
        await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    ).status_code == 200

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        await repository.set_totp_secret(b"encrypted-secret")
    user = await _compat_user()
    legacy = await _legacy_settings()
    assert user is not None
    assert user.totp_secret_encrypted == b"encrypted-secret" == legacy.totp_secret_encrypted
    assert user.totp_last_verified_step is None

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        assert await repository.try_advance_totp_last_verified_step(100) is True
        assert await repository.try_advance_totp_last_verified_step(100) is False  # replay
        assert await repository.try_advance_totp_last_verified_step(101) is True
    user = await _compat_user()
    legacy = await _legacy_settings()
    assert user is not None and user.totp_last_verified_step == 101 == legacy.totp_last_verified_step


@pytest.mark.asyncio
async def test_replay_counter_refuses_when_only_one_side_would_advance(async_client) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    # Simulate a peer that already consumed step 200 on the user row only.
    async with SessionLocal() as session:
        user = (await session.execute(select(DashboardUser))).scalar_one()
        user.totp_last_verified_step = 200
        await session.commit()

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        assert await repository.try_advance_totp_last_verified_step(200) is False
    legacy = await _legacy_settings()
    user = await _compat_user()
    assert legacy.totp_last_verified_step is None  # rolled back, not advanced on one side
    assert user is not None and user.totp_last_verified_step == 200


@pytest.mark.asyncio
async def test_identity_uniqueness_and_api_key_ownership_columns(db_setup) -> None:
    async with SessionLocal() as session:
        user = DashboardUser(username="alice", role_id=PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR])
        session.add(user)
        await session.flush()
        session.add(
            DashboardIdentity(user_id=user.id, provider="trusted_header", provider_key="default", subject="alice")
        )
        key = ApiKey(
            id=str(uuid.uuid4()),
            name="alice-key",
            key_hash="hash-1",
            key_prefix="sk-clb-alice",
            owner_user_id=user.id,
            created_by_user_id=user.id,
        )
        session.add(key)
        await session.commit()
        user_id, key_id = user.id, key.id

    async with SessionLocal() as session:
        session.add(
            DashboardIdentity(user_id=user_id, provider="trusted_header", provider_key="default", subject="alice")
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with SessionLocal() as session:
        stored = (await session.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one()
        assert stored.owner_user_id == user_id
        assert stored.deactivated_reason is None
        # Deleting the owner detaches the key (SET NULL) and cascades identities.
        owner = (await session.execute(select(DashboardUser).where(DashboardUser.id == user_id))).scalar_one()
        await session.delete(owner)
        await session.commit()
    async with SessionLocal() as session:
        stored = (await session.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one()
        assert stored.owner_user_id is None
        identities = (await session.execute(select(DashboardIdentity))).scalars().all()
        assert identities == []


@pytest.mark.parametrize("legacy_password_configured", [True, False])
@pytest.mark.asyncio
async def test_dashboard_users_migration_backfills_the_compat_admin(tmp_path, legacy_password_configured: bool):
    from alembic import command

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'dashboard-users.sqlite'}"

    await to_thread.run_sync(lambda: run_upgrade(db_url, PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            existing = (await conn.execute(text("SELECT id FROM dashboard_settings"))).first()
            if existing is None:
                await conn.execute(text("INSERT INTO dashboard_settings (id) VALUES (1)"))
            if legacy_password_configured:
                await conn.execute(
                    text(
                        "UPDATE dashboard_settings SET password_hash = :h, totp_secret_encrypted = :s, "
                        "totp_last_verified_step = :st WHERE id = 1"
                    ),
                    {"h": "$2b$legacy", "s": b"legacy-secret", "st": 42},
                )

        await to_thread.run_sync(lambda: run_upgrade(db_url, TARGET_REVISION, bootstrap_legacy=False))
        # Idempotency on pre-existing state: stamp back (no downgrade) and
        # re-run the same upgrade body over the already-created tables/rows.
        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.stamp(config, PARENT_REVISION))
        await to_thread.run_sync(lambda: run_upgrade(db_url, TARGET_REVISION, bootstrap_legacy=False))
        async with engine.connect() as conn:
            tables = {row[0] for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            assert {"dashboard_users", "dashboard_identities"} <= tables
            api_key_column_names = [row[1] for row in await conn.execute(text("PRAGMA table_info('api_keys')"))]
            api_key_columns = set(api_key_column_names)
            assert {"owner_user_id", "created_by_user_id", "deactivated_reason"} <= api_key_columns
            assert len(api_key_column_names) == len(api_key_columns)
            users = (
                await conn.execute(
                    text(
                        "SELECT id, username, role_id, status, is_break_glass, password_hash, "
                        "totp_secret_encrypted, totp_last_verified_step FROM dashboard_users"
                    )
                )
            ).all()
        if legacy_password_configured:
            assert len(users) == 1
            (row,) = users
            assert row[0] == COMPAT_ADMIN_USER_ID
            assert row[1] == COMPAT_ADMIN_USERNAME
            assert row[2] == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
            assert row[3] == "active"
            assert bool(row[4]) is True
            assert row[5] == "$2b$legacy"
            assert row[6] == b"legacy-secret"
            assert row[7] == 42
        else:
            assert users == []

        # The down/up walk validates the downgrade and a fresh re-apply.
        await to_thread.run_sync(lambda: command.downgrade(config, PARENT_REVISION))
        async with engine.connect() as conn:
            tables = {row[0] for row in await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
            api_key_columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('api_keys')"))}
        assert not {"dashboard_users", "dashboard_identities"} & tables
        assert not {"owner_user_id", "created_by_user_id", "deactivated_reason"} & api_key_columns

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
        async with engine.connect() as conn:
            count = (await conn.execute(text("SELECT COUNT(*) FROM dashboard_users"))).scalar_one()
        assert count == (1 if legacy_password_configured else 0)
    finally:
        await engine.dispose()
