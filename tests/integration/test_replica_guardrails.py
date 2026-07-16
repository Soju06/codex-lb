from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.auth.guardian import _count_live_bridge_ring_members
from app.core.config.key_fingerprint import (
    ENCRYPTION_KEY_FINGERPRINT_SENTINEL,
    EncryptionKeyFingerprintMismatchError,
    compute_encryption_key_fingerprint,
    verify_encryption_key_fingerprint,
)
from app.core.exceptions import DashboardSettingsConflictError
from app.core.utils.time import utcnow
from app.db.models import BridgeRingMember, RuntimeSentinel
from app.db.session import SessionLocal
from app.modules.dashboard_auth.repository import DashboardAuthRepository
from app.modules.proxy.ring_membership import RING_STALE_THRESHOLD_SECONDS
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.service import SettingsService

pytestmark = pytest.mark.integration


async def _stored_sentinel_values() -> list[str]:
    async with SessionLocal() as session:
        result = await session.execute(
            select(RuntimeSentinel.value).where(RuntimeSentinel.name == ENCRYPTION_KEY_FINGERPRINT_SENTINEL)
        )
        return [row[0] for row in result.all()]


async def _stamp_sentinel(value: str) -> None:
    async with SessionLocal() as session:
        session.add(RuntimeSentinel(name=ENCRYPTION_KEY_FINGERPRINT_SENTINEL, value=value))
        await session.commit()


# --- Auth Guardian dynamic bridge-ring detection ---


@pytest.mark.asyncio
async def test_count_live_bridge_ring_members_counts_only_fresh_heartbeats(db_setup):
    now = utcnow()
    stale = now - timedelta(seconds=RING_STALE_THRESHOLD_SECONDS + 60)
    async with SessionLocal() as session:
        session.add_all(
            [
                BridgeRingMember(instance_id="pod-a", registered_at=now, last_heartbeat_at=now),
                BridgeRingMember(instance_id="pod-b", registered_at=now, last_heartbeat_at=now),
                BridgeRingMember(instance_id="pod-dead", registered_at=stale, last_heartbeat_at=stale),
            ]
        )
        await session.commit()

    # Two replicas are live in the DB-backed ring even though the static
    # instance-ring env var is empty; the stale row is excluded.
    assert await _count_live_bridge_ring_members() == 2


# --- Encryption-key fingerprint sentinel ---


@pytest.mark.asyncio
async def test_fingerprint_first_boot_stamps_sentinel(db_setup):
    await verify_encryption_key_fingerprint()

    values = await _stored_sentinel_values()
    assert values == [compute_encryption_key_fingerprint()]
    assert values[0].startswith("sha256:")


@pytest.mark.asyncio
async def test_fingerprint_matching_replica_passes(db_setup):
    await verify_encryption_key_fingerprint()
    # Second replica with the same key material re-runs the startup check.
    await verify_encryption_key_fingerprint()

    assert await _stored_sentinel_values() == [compute_encryption_key_fingerprint()]


@pytest.mark.asyncio
async def test_fingerprint_divergent_replica_refuses_startup_in_enforce_mode(db_setup, tmp_path: Path):
    await verify_encryption_key_fingerprint()
    stamped = compute_encryption_key_fingerprint()
    divergent_key_file = tmp_path / "replica-b-encryption.key"

    with pytest.raises(EncryptionKeyFingerprintMismatchError) as exc_info:
        await verify_encryption_key_fingerprint(key_file=divergent_key_file)

    message = str(exc_info.value)
    local = compute_encryption_key_fingerprint(divergent_key_file)
    assert local[: len("sha256:") + 12] in message
    assert stamped[: len("sha256:") + 12] in message
    assert "same encryption.key" in message
    assert "runtime_sentinels" in message
    # The winner's stamp is untouched.
    assert await _stored_sentinel_values() == [stamped]


@pytest.mark.asyncio
async def test_fingerprint_divergent_replica_continues_in_warn_mode(db_setup, tmp_path: Path, caplog):
    await verify_encryption_key_fingerprint()
    divergent_key_file = tmp_path / "replica-b-encryption.key"

    with caplog.at_level(logging.ERROR, logger="app.core.config.key_fingerprint"):
        await verify_encryption_key_fingerprint(key_file=divergent_key_file, mode="warn")

    assert any(
        record.levelno == logging.ERROR and "Encryption key mismatch across replicas" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_fingerprint_off_mode_skips_check(db_setup, tmp_path: Path):
    await verify_encryption_key_fingerprint(key_file=tmp_path / "replica-b-encryption.key", mode="off")

    assert await _stored_sentinel_values() == []


@pytest.mark.asyncio
async def test_fingerprint_concurrent_first_boot_stamps_exactly_once(db_setup, tmp_path: Path):
    key_a = tmp_path / "replica-a-encryption.key"
    key_b = tmp_path / "replica-b-encryption.key"

    results = await asyncio.gather(
        verify_encryption_key_fingerprint(key_file=key_a),
        verify_encryption_key_fingerprint(key_file=key_b),
        return_exceptions=True,
    )

    failures = [result for result in results if isinstance(result, EncryptionKeyFingerprintMismatchError)]
    assert len(failures) == 1
    values = await _stored_sentinel_values()
    assert len(values) == 1
    assert values[0] in {
        compute_encryption_key_fingerprint(key_a),
        compute_encryption_key_fingerprint(key_b),
    }


@pytest.mark.asyncio
async def test_lifespan_refuses_startup_on_divergent_fingerprint(app_instance):
    await _stamp_sentinel("sha256:" + "0" * 64)

    with pytest.raises(EncryptionKeyFingerprintMismatchError):
        async with app_instance.router.lifespan_context(app_instance):
            pytest.fail("startup must not complete with a divergent encryption-key fingerprint")


# --- Dashboard settings optimistic locking ---


@pytest.mark.asyncio
async def test_settings_version_exposed_and_incremented(async_client):
    response = await async_client.get("/api/settings")
    assert response.status_code == 200
    initial_version = response.json()["version"]
    assert initial_version >= 1

    response = await async_client.put("/api/settings", json={"stickyThreadsEnabled": False})
    assert response.status_code == 200
    assert response.json()["version"] == initial_version + 1

    response = await async_client.get("/api/settings")
    assert response.json()["version"] == initial_version + 1


@pytest.mark.asyncio
async def test_settings_put_stale_expected_version_conflicts_without_write(async_client):
    response = await async_client.get("/api/settings")
    current_version = response.json()["version"]
    assert response.json()["stickyThreadsEnabled"] is True

    response = await async_client.put(
        "/api/settings",
        json={"expectedVersion": current_version + 1, "stickyThreadsEnabled": False},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "settings_conflict"
    response = await async_client.get("/api/settings")
    assert response.json()["stickyThreadsEnabled"] is True
    assert response.json()["version"] == current_version


@pytest.mark.asyncio
async def test_settings_put_current_expected_version_succeeds(async_client):
    response = await async_client.get("/api/settings")
    current_version = response.json()["version"]

    response = await async_client.put(
        "/api/settings",
        json={"expectedVersion": current_version, "stickyThreadsEnabled": False},
    )

    assert response.status_code == 200
    assert response.json()["stickyThreadsEnabled"] is False
    assert response.json()["version"] == current_version + 1


@pytest.mark.asyncio
async def test_concurrent_settings_put_loser_receives_409(async_client, monkeypatch):
    """Two racing PUT /api/settings writers that read the same version: one wins, one gets 409."""
    original_commit = SettingsRepository.commit_refresh
    first_writer_reached_commit = asyncio.Event()
    second_writer_committed = asyncio.Event()
    call_count = 0

    async def racing_commit(self, settings):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Writer A pauses after reading the settings version and applying its
            # mutation but before committing, letting writer B (which read the
            # same version) commit first — the cross-replica interleaving.
            first_writer_reached_commit.set()
            await asyncio.wait_for(second_writer_committed.wait(), timeout=10)
        return await original_commit(self, settings)

    monkeypatch.setattr(SettingsRepository, "commit_refresh", racing_commit)

    task_a = asyncio.create_task(async_client.put("/api/settings", json={"stickyThreadsEnabled": False}))
    await asyncio.wait_for(first_writer_reached_commit.wait(), timeout=10)
    response_b = await async_client.put("/api/settings", json={"prohibitFastMode": True})
    second_writer_committed.set()
    response_a = await task_a

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["error"]["code"] == "settings_conflict"
    # The winner's write persisted; the loser wrote nothing.
    response = await async_client.get("/api/settings")
    assert response.json()["prohibitFastMode"] is True
    assert response.json()["stickyThreadsEnabled"] is True


@pytest.mark.asyncio
async def test_concurrent_no_op_settings_put_loser_receives_409(async_client, monkeypatch):
    """A no-op save (payload equals the writer's own stale row) must still lose
    with 409 when a concurrent writer commits first. Without forcing the CAS,
    the loser's flush emits no ORM UPDATE, so `version_id_col` never fires and
    the stale save silently succeeds over the winner's fields."""
    original_commit = SettingsRepository.commit_refresh
    first_writer_reached_commit = asyncio.Event()
    second_writer_committed = asyncio.Event()
    call_count = 0

    async def racing_commit(self, settings):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Writer A read the row and applied a no-op payload, then pauses
            # before committing so writer B (which read the same version) can
            # commit a real change first.
            first_writer_reached_commit.set()
            await asyncio.wait_for(second_writer_committed.wait(), timeout=10)
        return await original_commit(self, settings)

    monkeypatch.setattr(SettingsRepository, "commit_refresh", racing_commit)

    response = await async_client.get("/api/settings")
    assert response.json()["stickyThreadsEnabled"] is True

    # Writer A submits a payload that leaves every field at its current value.
    task_a = asyncio.create_task(async_client.put("/api/settings", json={"stickyThreadsEnabled": True}))
    await asyncio.wait_for(first_writer_reached_commit.wait(), timeout=10)
    response_b = await async_client.put("/api/settings", json={"prohibitFastMode": True})
    second_writer_committed.set()
    response_a = await task_a

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["error"]["code"] == "settings_conflict"
    # Writer B's real change survived; the stale no-op did not revert it.
    response = await async_client.get("/api/settings")
    assert response.json()["prohibitFastMode"] is True


@pytest.mark.asyncio
async def test_settings_put_conflicts_when_writer_commits_between_check_and_update(async_client, monkeypatch):
    """A writer committing after the expectedVersion check but before the service
    update must still lose with 409: the handler passes the version it merged
    omitted fields from as `expected_version`, the repository rejects a row that
    has moved past it, and the versioned UPDATE (`WHERE version = :expected`)
    covers the residual read-to-commit window."""
    original_update = SettingsService.update_settings
    first_writer_passed_check = asyncio.Event()
    second_writer_committed = asyncio.Event()
    call_count = 0

    async def racing_update(self, payload, *, expected_version=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Writer A has already passed the api-level expectedVersion check
            # (it runs before service.update_settings); pause before the
            # service touches the row again so writer B can commit in between.
            first_writer_passed_check.set()
            await asyncio.wait_for(second_writer_committed.wait(), timeout=10)
        return await original_update(self, payload, expected_version=expected_version)

    monkeypatch.setattr(SettingsService, "update_settings", racing_update)

    response = await async_client.get("/api/settings")
    version = response.json()["version"]

    task_a = asyncio.create_task(
        async_client.put(
            "/api/settings",
            json={"expectedVersion": version, "stickyThreadsEnabled": False},
        )
    )
    await asyncio.wait_for(first_writer_passed_check.wait(), timeout=10)
    response_b = await async_client.put(
        "/api/settings",
        json={"expectedVersion": version, "prohibitFastMode": True},
    )
    second_writer_committed.set()
    response_a = await task_a

    assert response_b.status_code == 200
    assert response_a.status_code == 409
    assert response_a.json()["error"]["code"] == "settings_conflict"
    # Writer B's committed fields survived; writer A wrote nothing.
    response = await async_client.get("/api/settings")
    assert response.json()["prohibitFastMode"] is True
    assert response.json()["stickyThreadsEnabled"] is True
    assert response.json()["version"] == version + 1


@pytest.mark.asyncio
async def test_dashboard_auth_writer_retries_through_version_conflict(db_setup):
    async with SessionLocal() as session_a, SessionLocal() as session_b:
        auth_repo = DashboardAuthRepository(session_a)
        # Replica A loads the settings row (and its version) first.
        row_a = await auth_repo.get_settings()
        version_before = row_a.version

        # Replica B commits a settings update in between, bumping the version.
        settings_repo_b = SettingsRepository(session_b)
        row_b = await settings_repo_b.get_or_create()
        row_b.sticky_threads_enabled = False
        await settings_repo_b.commit_refresh(row_b)
        assert row_b.version == version_before + 1

        # Replica A's credential mutation hits the version conflict and retries.
        row = await auth_repo.set_guest_password_hash("guest-hash")
        assert row.guest_password_hash == "guest-hash"
        # Replica B's write survived: the retry re-read instead of clobbering.
        assert row.sticky_threads_enabled is False
        assert row.version == version_before + 2


@pytest.mark.asyncio
async def test_settings_repository_conflict_maps_to_dashboard_settings_conflict(db_setup):
    async with SessionLocal() as session_a, SessionLocal() as session_b:
        repo_a = SettingsRepository(session_a)
        repo_b = SettingsRepository(session_b)
        row_a = await repo_a.get_or_create()
        row_b = await repo_b.get_or_create()

        row_b.prohibit_fast_mode = True
        await repo_b.commit_refresh(row_b)

        row_a.sticky_threads_enabled = False
        with pytest.raises(DashboardSettingsConflictError):
            await repo_a.commit_refresh(row_a)
