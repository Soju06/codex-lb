"""Schema and backfill tests for the per-user account tables.

``dashboard_users`` is the sole authority for every dashboard credential. These
tests prove (a) the migrations create the tables, backfill the existing
credential and re-project it once before the switch, (b) a credential write has
exactly one destination and leaves ``dashboard_settings`` alone, and (c) the
TOTP replay counter still refuses a reused code with only the account row left.
"""

from __future__ import annotations

import uuid

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings import get_settings
from app.core.exceptions import DashboardSettingsConflictError
from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade
from app.db.models import (
    COMPAT_ADMIN_USER_ID,
    COMPAT_ADMIN_USERNAME,
    ApiKey,
    DashboardIdentity,
    DashboardSettings,
    DashboardUser,
)
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store
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


async def _settings_row() -> DashboardSettings:
    async with SessionLocal() as session:
        return (await session.execute(select(DashboardSettings))).scalar_one()


async def _settings_snapshot() -> dict[str, object]:
    """Every ``dashboard_settings`` value a credential write could plausibly touch."""

    row = await _settings_row()
    return {
        "guest_password_hash": row.guest_password_hash,
        "guest_session_generation": row.guest_session_generation,
        "bootstrap_token_encrypted": row.bootstrap_token_encrypted,
        "bootstrap_token_hash": row.bootstrap_token_hash,
        "totp_required_on_login": row.totp_required_on_login,
        "totp_required_for_admin_role": row.totp_required_for_admin_role,
        "local_login_policy": row.local_login_policy,
    }


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
    assert user.password_hash is not None

    # A second setup attempt is refused and leaves exactly one admin row.
    again = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password456"})
    assert again.status_code == 409
    async with SessionLocal() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM dashboard_users"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_password_change_and_removal_write_only_the_account_row(async_client) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    login = await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    assert login.status_code == 200
    before = await _settings_snapshot()

    changed = await async_client.post(
        "/api/dashboard-auth/password/change",
        json={"currentPassword": "password123", "newPassword": "password456"},
    )
    assert changed.status_code == 200, changed.text
    user = await _compat_user()
    assert user is not None and user.password_hash is not None
    # The password write moved nothing install-wide except the bootstrap token,
    # which setup had already cleared.
    assert await _settings_snapshot() == before

    removed = await async_client.request("DELETE", "/api/dashboard-auth/password", json={"password": "password456"})
    assert removed.status_code == 200, removed.text
    user = await _compat_user()
    assert user is not None
    assert user.password_hash is None
    assert user.totp_secret_encrypted is None

    # Setting a password again re-arms the surviving account row instead of
    # failing on the unique username.
    again = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password789"})
    assert again.status_code == 200, again.text
    user = await _compat_user()
    assert user is not None and user.id == COMPAT_ADMIN_USER_ID
    assert user.password_hash is not None
    async with SessionLocal() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM dashboard_users"))).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_a_settings_version_conflict_re_applies_the_whole_password_write(async_client, monkeypatch) -> None:
    """The account row and the bootstrap-token clear commit together or not at all."""

    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    async with SessionLocal() as session:
        row = await SettingsRepository(session).get_or_create()
        row.bootstrap_token_encrypted, row.bootstrap_token_hash = b"token", b"token-hash"
        await session.commit()

    original_commit_refresh = SettingsRepository.commit_refresh
    attempts = {"count": 0}

    async def _conflict_once(self, settings, *, on_committed=None):
        attempts["count"] += 1
        if attempts["count"] == 1:
            # A concurrent settings writer won the optimistic version race:
            # commit_refresh rolls the whole transaction back, including the
            # flushed account-row update.
            await self._session.rollback()
            raise DashboardSettingsConflictError()
        await original_commit_refresh(self, settings, on_committed=on_committed)

    monkeypatch.setattr(SettingsRepository, "commit_refresh", _conflict_once)
    admin = await _compat_user()
    assert admin is not None
    async with SessionLocal() as session:
        await DashboardAuthRepository(session).rotate_user_password(admin.id, "$2b$retried")
    assert attempts["count"] == 2

    user = await _compat_user()
    assert user is not None and user.password_hash == "$2b$retried"
    row = await _settings_row()
    assert row.bootstrap_token_encrypted is None and row.bootstrap_token_hash is None


@pytest.mark.asyncio
async def test_totp_secret_and_replay_counter_live_on_the_account_row(async_client) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    assert (
        await async_client.post("/api/dashboard-auth/password/login", json={"password": "password123"})
    ).status_code == 200

    admin = await _compat_user()
    assert admin is not None
    before = await _settings_snapshot()
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        await repository.set_user_totp_secret(admin.id, b"encrypted-secret")
    user = await _compat_user()
    assert user is not None
    assert user.totp_secret_encrypted == b"encrypted-secret"
    assert user.totp_last_verified_step is None
    assert await _settings_snapshot() == before

    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        assert await repository.try_advance_user_totp_step(admin.id, 100) is True
        assert await repository.try_advance_user_totp_step(admin.id, 100) is False  # replay
        assert await repository.try_advance_user_totp_step(admin.id, 101) is True
    user = await _compat_user()
    assert user is not None and user.totp_last_verified_step == 101
    assert await _settings_snapshot() == before


@pytest.mark.asyncio
async def test_the_conditional_update_is_what_refuses_a_replay(async_client) -> None:
    """One row left, and it is still the ``WHERE`` clause that decides.

    A code whose step the account row has already reached changes zero rows;
    the refusal rolls the transaction back, so a caller that had flushed
    anything else in the same session keeps nothing.
    """

    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    # A peer consumed step 200 a moment ago.
    async with SessionLocal() as session:
        user = (await session.execute(select(DashboardUser))).scalar_one()
        user.totp_last_verified_step = 200
        await session.commit()

    admin = await _compat_user()
    assert admin is not None
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        assert await repository.try_advance_user_totp_step(admin.id, 200) is False
        # An earlier step is a replay too, and so is a step already passed.
        assert await repository.try_advance_user_totp_step(admin.id, 199) is False
        assert await repository.try_advance_user_totp_step(admin.id, 201) is True
    user = await _compat_user()
    assert user is not None and user.totp_last_verified_step == 201


@pytest.mark.asyncio
async def test_a_refused_replay_rolls_back_the_whole_transaction(async_client) -> None:
    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    admin = await _compat_user()
    assert admin is not None
    async with SessionLocal() as session:
        repository = DashboardAuthRepository(session)
        assert await repository.try_advance_user_totp_step(admin.id, 400) is True
        # Something else written in the same session before the refusal must
        # not survive it: the refusal is a rollback, not a skipped statement.
        session.add(DashboardUser(username="ghost", role_id=PRESET_ROLE_IDS[PresetRoleSlug.VIEWER]))
        await session.flush()
        assert await repository.try_advance_user_totp_step(admin.id, 400) is False
    async with SessionLocal() as session:
        assert (await session.execute(select(DashboardUser).where(DashboardUser.username == "ghost"))).first() is None
        stored = (await session.execute(select(DashboardUser).where(DashboardUser.id == admin.id))).scalar_one()
    assert stored.totp_last_verified_step == 400


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


REPROJECT_REVISION = "20260909_020000_reproject_compat_admin_credentials"
DROP_LEGACY_REVISION = "20260912_010000_drop_legacy_dashboard_credentials"
DROP_LEGACY_PARENT = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
#: What the drop takes, and what it must leave alone.
_DROPPED_LEGACY_COLUMNS = {"password_hash", "totp_secret_encrypted", "totp_last_verified_step"}
_SURVIVING_CREDENTIAL_COLUMNS = {
    "guest_password_hash",
    "guest_session_generation",
    "bootstrap_token_encrypted",
    "bootstrap_token_hash",
    "totp_required_on_login",
    "totp_required_for_admin_role",
    "local_login_policy",
}


def test_the_credential_revisions_are_on_the_single_head_path() -> None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_build_alembic_config(get_settings().database_url))
    assert script.get_heads() == [_HEAD_REVISION]
    chain = {revision.revision for revision in script.iterate_revisions(_HEAD_REVISION, "base")}
    assert REPROJECT_REVISION in chain
    # The drop is only safe after the reprojection has run, so it must descend
    # from it rather than merely share a head with it.
    assert DROP_LEGACY_REVISION in chain
    assert REPROJECT_REVISION in {
        revision.revision for revision in script.iterate_revisions(DROP_LEGACY_REVISION, "base")
    }


async def _settings_columns(engine) -> set[str]:
    async with engine.connect() as conn:
        return {row[1] for row in await conn.execute(text("PRAGMA table_info('dashboard_settings')"))}


@pytest.mark.asyncio
async def test_the_drop_revision_takes_exactly_the_three_projected_columns(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'drop-legacy.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, DROP_LEGACY_PARENT, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        before = await _settings_columns(engine)
        assert _DROPPED_LEGACY_COLUMNS <= before and _SURVIVING_CREDENTIAL_COLUMNS <= before
        async with engine.begin() as conn:
            if (await conn.execute(text("SELECT id FROM dashboard_settings"))).first() is None:
                await conn.execute(text("INSERT INTO dashboard_settings (id) VALUES (1)"))
            await conn.execute(
                text(
                    "UPDATE dashboard_settings SET password_hash = :h, totp_secret_encrypted = :s, "
                    "totp_last_verified_step = 42, guest_password_hash = :g, bootstrap_token_hash = :b, "
                    "totp_required_on_login = 1, totp_required_for_admin_role = 1 WHERE id = 1"
                ),
                {"h": "$2b$legacy", "s": b"legacy-secret", "g": "$2b$guest", "b": b"token-hash"},
            )

        await to_thread.run_sync(lambda: run_upgrade(db_url, DROP_LEGACY_REVISION, bootstrap_legacy=False))
        after = await _settings_columns(engine)
        assert not _DROPPED_LEGACY_COLUMNS & after
        assert _SURVIVING_CREDENTIAL_COLUMNS <= after
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT guest_password_hash, bootstrap_token_hash, totp_required_on_login, "
                        "totp_required_for_admin_role, local_login_policy FROM dashboard_settings WHERE id = 1"
                    )
                )
            ).one()
        assert row[0] == "$2b$guest" and row[1] == b"token-hash"
        assert bool(row[2]) is True and bool(row[3]) is True and row[4] == "enabled"

        # The upgrade body converges when the columns are already gone.
        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.stamp(config, DROP_LEGACY_PARENT))
        await to_thread.run_sync(lambda: run_upgrade(db_url, DROP_LEGACY_REVISION, bootstrap_legacy=False))
        assert await _settings_columns(engine) == after
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_downgrade_re_projects_from_a_renamed_account_by_id(tmp_path) -> None:
    """After this release the bootstrap account's name is not a handle; its id is."""

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'drop-legacy-down.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, DROP_LEGACY_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            if (await conn.execute(text("SELECT id FROM dashboard_settings"))).first() is None:
                await conn.execute(text("INSERT INTO dashboard_settings (id) VALUES (1)"))
            await conn.execute(
                text(
                    "INSERT INTO dashboard_users (id, username, role_id, role_source, status, password_hash, "
                    "totp_secret_encrypted, totp_last_verified_step, session_generation, must_change_password, "
                    "is_break_glass) VALUES (:id, 'alice', :role, 'manual', 'active', :h, :s, 77, 0, 0, 1)"
                ),
                {
                    "id": COMPAT_ADMIN_USER_ID,
                    "role": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
                    "h": "$2b$renamed",
                    "s": b"renamed-secret",
                },
            )

        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, DROP_LEGACY_PARENT))
        assert _DROPPED_LEGACY_COLUMNS <= await _settings_columns(engine)
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT password_hash, totp_secret_encrypted, totp_last_verified_step "
                        "FROM dashboard_settings WHERE id = 1"
                    )
                )
            ).one()
        assert row == ("$2b$renamed", b"renamed-secret", 77)

        # The downgrade body converges when the columns are already present.
        await to_thread.run_sync(lambda: command.stamp(config, DROP_LEGACY_REVISION))
        await to_thread.run_sync(lambda: command.downgrade(config, DROP_LEGACY_PARENT))
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_downgrade_leaves_the_columns_null_when_the_account_is_gone(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'drop-legacy-empty.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, DROP_LEGACY_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            if (await conn.execute(text("SELECT id FROM dashboard_settings"))).first() is None:
                await conn.execute(text("INSERT INTO dashboard_settings (id) VALUES (1)"))
        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, DROP_LEGACY_PARENT))
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT password_hash, totp_secret_encrypted, totp_last_verified_step "
                        "FROM dashboard_settings WHERE id = 1"
                    )
                )
            ).one()
        assert row == (None, None, None)
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "case",
    [
        "stale_user_row_is_overwritten",
        "legacy_null_clears_user_credentials",
        "missing_user_row_is_created",
        "no_settings_row",
    ],
)
@pytest.mark.asyncio
async def test_reproject_migration_makes_the_user_row_match_the_legacy_credential(tmp_path, case: str):
    """The previous release may have written credentials only to ``dashboard_settings``.

    Before the user row becomes authoritative, the legacy credential is copied
    onto the compat ``admin`` row one last time (or cleared when the legacy
    password was removed). Nothing else about the row changes.
    """

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'reproject.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            if case == "no_settings_row":
                await conn.execute(text("DELETE FROM dashboard_settings"))
            else:
                existing = (await conn.execute(text("SELECT id FROM dashboard_settings"))).first()
                if existing is None:
                    await conn.execute(text("INSERT INTO dashboard_settings (id) VALUES (1)"))
                if case != "missing_user_row_is_created":
                    await conn.execute(
                        text("UPDATE dashboard_settings SET password_hash = '$2b$old', totp_last_verified_step = 1"),
                    )
        # The previous revision backfills the row from the legacy credential as it stood then.
        await to_thread.run_sync(lambda: run_upgrade(db_url, TARGET_REVISION, bootstrap_legacy=False))

        async with engine.begin() as conn:
            if case == "stale_user_row_is_overwritten":
                await conn.execute(text("UPDATE dashboard_users SET session_generation = 5"))
                await conn.execute(
                    text(
                        "UPDATE dashboard_settings SET password_hash = '$2b$new', totp_secret_encrypted = :s, "
                        "totp_last_verified_step = 77"
                    ),
                    {"s": b"new-secret"},
                )
            elif case == "legacy_null_clears_user_credentials":
                await conn.execute(
                    text(
                        "UPDATE dashboard_settings SET password_hash = NULL, totp_secret_encrypted = NULL, "
                        "totp_last_verified_step = NULL"
                    )
                )
            elif case == "missing_user_row_is_created":
                await conn.execute(text("UPDATE dashboard_settings SET password_hash = '$2b$late'"))

        await to_thread.run_sync(lambda: run_upgrade(db_url, REPROJECT_REVISION, bootstrap_legacy=False))

        async with engine.connect() as conn:
            users = (
                await conn.execute(
                    text(
                        "SELECT id, username, password_hash, totp_secret_encrypted, totp_last_verified_step, "
                        "session_generation, is_break_glass, status FROM dashboard_users"
                    )
                )
            ).all()
        if case == "no_settings_row":
            assert users == []
        else:
            assert len(users) == 1
            (row,) = users
            assert row[0] == COMPAT_ADMIN_USER_ID and row[1] == COMPAT_ADMIN_USERNAME
            assert row[7] == "active" and bool(row[6]) is True
            if case == "stale_user_row_is_overwritten":
                assert (row[2], row[3], row[4]) == ("$2b$new", b"new-secret", 77)
                assert row[5] == 5  # session_generation untouched
            elif case == "legacy_null_clears_user_credentials":
                assert (row[2], row[3], row[4]) == (None, None, None)
            else:
                assert (row[2], row[3], row[4]) == ("$2b$late", None, None)

        # Data-only: downgrading past it and coming back to head leaves the schema intact.
        config = _build_alembic_config(db_url)
        await to_thread.run_sync(lambda: command.downgrade(config, TARGET_REVISION))
        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == _HEAD_REVISION
    finally:
        await engine.dispose()


async def _insert_operator(username: str = "ops") -> DashboardUser:
    async with SessionLocal() as session:
        user = DashboardUser(
            username=username, role_id=PRESET_ROLE_IDS[PresetRoleSlug.OPERATOR], password_hash="$2b$ops"
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    await get_dashboard_users_cache().invalidate()
    return user


@pytest.mark.asyncio
async def test_a_credential_write_behaves_the_same_whatever_the_account_is_called(async_client) -> None:
    """The bootstrap account renamed, and an ordinary one: one code path, one destination."""

    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    async with SessionLocal() as session:
        bootstrap = (await session.execute(select(DashboardUser))).scalar_one()
        bootstrap.username = "alice"
        await session.commit()
    await get_dashboard_users_cache().invalidate()
    ops = await _insert_operator()

    for user_id in (COMPAT_ADMIN_USER_ID, ops.id):
        before = await _settings_snapshot()
        async with SessionLocal() as session:
            repository = DashboardAuthRepository(session)
            await repository.set_user_totp_secret(user_id, b"a-secret")
            assert await repository.try_advance_user_totp_step(user_id, 500) is True
            assert await repository.try_advance_user_totp_step(user_id, 500) is False
            await repository.rotate_user_password(user_id, "$2b$rotated")
        # Two active accounts: neither one's self-service write moves an
        # install-wide requirement, and neither writes a credential anywhere
        # but its own row.
        assert await _settings_snapshot() == before
        async with SessionLocal() as session:
            stored = await session.get(DashboardUser, user_id)
        assert stored is not None
        assert stored.password_hash == "$2b$rotated"
        assert stored.totp_secret_encrypted == b"a-secret"
        assert stored.totp_last_verified_step == 500


@pytest.mark.asyncio
async def test_session_generation_bump_is_atomic_across_stale_sessions(async_client) -> None:
    """Two repositories holding the same stale row must still advance the counter twice."""

    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    admin = await _compat_user()
    assert admin is not None and admin.session_generation == 0

    async with SessionLocal() as session_a, SessionLocal() as session_b:
        repo_a, repo_b = DashboardAuthRepository(session_a), DashboardAuthRepository(session_b)
        assert (await repo_a.get_user_by_id(admin.id)) is not None
        assert (await repo_b.get_user_by_id(admin.id)) is not None  # both sessions now cache generation 0
        assert await repo_a.bump_session_generation(admin.id) == 1
        assert await repo_b.bump_session_generation(admin.id) == 2

    stored = await _compat_user()
    assert stored is not None and stored.session_generation == 2
    await get_dashboard_users_cache().invalidate()

    store = get_dashboard_session_store()
    intermediate = store.create_user_session(admin.id, 1, password_verified=True, totp_verified=False, ttl_seconds=600)
    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, intermediate)
    assert (await async_client.get("/api/settings")).status_code == 401
    current = store.create_user_session(admin.id, 2, password_verified=True, totp_verified=False, ttl_seconds=600)
    async_client.cookies.set(DASHBOARD_SESSION_COOKIE, current)
    assert (await async_client.get("/api/settings")).status_code == 200


@pytest.mark.asyncio
async def test_password_rotation_is_all_or_nothing(async_client, monkeypatch) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert (
        await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    ).status_code == 200
    admin = await _compat_user()
    assert admin is not None
    original_hash, original_generation = admin.password_hash, admin.session_generation
    async with SessionLocal() as session:
        row = await SettingsRepository(session).get_or_create()
        row.bootstrap_token_encrypted, row.bootstrap_token_hash = b"token", b"token-hash"
        await session.commit()

    real_commit = AsyncSession.commit
    failures = {"remaining": 1}

    async def flaky_commit(self: AsyncSession) -> None:
        if failures["remaining"]:
            failures["remaining"] -= 1
            raise RuntimeError("simulated commit failure")
        await real_commit(self)

    monkeypatch.setattr(AsyncSession, "commit", flaky_commit)
    async with SessionLocal() as session:
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await DashboardAuthRepository(session).rotate_user_password(admin.id, "$2b$new")

    after_failure = await _compat_user()
    assert after_failure is not None
    assert after_failure.password_hash == original_hash
    assert after_failure.session_generation == original_generation
    row = await _settings_row()
    assert (row.bootstrap_token_encrypted, row.bootstrap_token_hash) == (b"token", b"token-hash")

    async with SessionLocal() as session:
        rotated = await DashboardAuthRepository(session).rotate_user_password(admin.id, "$2b$new")
    assert rotated.password_hash == "$2b$new"
    assert rotated.session_generation == original_generation + 1
    row = await _settings_row()
    assert row.bootstrap_token_encrypted is None and row.bootstrap_token_hash is None


@pytest.mark.asyncio
async def test_replaying_the_whole_chain_over_a_dropped_schema_keeps_the_credentials(tmp_path) -> None:
    """A lost ledger must not cost the install its password.

    Re-applying from base re-creates the legacy columns empty a few revisions
    before dropping them again; the one-shot re-projection in between would
    read that emptiness as "the password was removed" and clear the account row
    -- which by then holds the only credential there is.
    """

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'replay.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
    engine = create_async_engine(db_url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO dashboard_users (id, username, role_id, role_source, status, password_hash, "
                    "totp_secret_encrypted, totp_last_verified_step, session_generation, must_change_password, "
                    "is_break_glass) VALUES (:id, 'alice', :role, 'manual', 'active', :h, :s, 12, 3, 0, 1)"
                ),
                {
                    "id": COMPAT_ADMIN_USER_ID,
                    "role": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
                    "h": "$2b$only-copy",
                    "s": b"only-secret",
                },
            )
            await conn.execute(text("DROP TABLE alembic_version"))

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=True))
        assert result.current_revision == _HEAD_REVISION
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT username, password_hash, totp_secret_encrypted, totp_last_verified_step, "
                        "session_generation FROM dashboard_users WHERE id = :id"
                    ),
                    {"id": COMPAT_ADMIN_USER_ID},
                )
            ).one()
        assert row == ("alice", "$2b$only-copy", b"only-secret", 12, 3)
        assert not _DROPPED_LEGACY_COLUMNS & await _settings_columns(engine)
    finally:
        await engine.dispose()
