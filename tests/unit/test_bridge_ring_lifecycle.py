from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.selectable import Select

import app.modules.proxy.durable_bridge_coordinator as durable_bridge_coordinator_module
import app.modules.proxy.durable_bridge_repository as durable_repo_module
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import Settings
from app.core.utils.time import utcnow
from app.db.models import (
    HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
    HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1,
    AccountStatus,
    Base,
    BridgeRingMember,
    HttpBridgeOperationEvent,
    HttpBridgeOperationEventChunk,
    HttpBridgeOperationRecord,
    HttpBridgeRetryCircuit,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
)
from app.modules.proxy import durable_bridge_repository as durable_bridge_repository_module
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_allow_durable_takeover,
    _http_bridge_claim_allows_takeover,
    _http_bridge_durable_lookup_allows_turn_state_takeover,
)
from app.modules.proxy.continuity import make_http_bridge_account_neutral_replay_key
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import (
    _PROTECTED_OPERATION_ID_SAFE_LIMIT,
    _PROTECTED_OPERATION_SCAN_BUDGET,
    DurableBridgeAliasRegistration,
    DurableBridgeOperationEventInput,
    DurableBridgeRepository,
    durable_bridge_hash,
    durable_bridge_operation_id,
    missing_durable_bridge_tables,
)
from app.modules.proxy.durable_bridge_transcript_codec import encode_durable_bridge_transcript_chunk
from app.modules.proxy.http_bridge_event_batcher import HttpBridgeOperationEventBatcher
from app.modules.proxy.ring_membership import RingMembershipService

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _share_proxy_dashboard_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SettingsCache:
        async def get(self) -> object:
            return proxy_service.get_settings()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())


@pytest.mark.asyncio
async def test_operation_event_reader_uses_configured_spool_limit(monkeypatch) -> None:
    session = AsyncMock()
    repository = AsyncMock()
    repository.get_operation_events = AsyncMock(return_value=["event"])
    close_session = AsyncMock()
    max_bytes = 9 * 1024 * 1024
    monkeypatch.setattr(durable_bridge_coordinator_module, "DurableBridgeRepository", lambda _session: repository)
    monkeypatch.setattr(durable_bridge_coordinator_module, "close_session", close_session)
    monkeypatch.setattr(
        durable_bridge_coordinator_module,
        "get_settings",
        lambda: SimpleNamespace(http_responses_session_bridge_operation_event_spool_max_bytes=max_bytes),
    )
    coordinator = DurableBridgeSessionCoordinator(lambda: session)

    assert await coordinator.get_operation_events(operation_id="operation") == ["event"]

    repository.get_operation_events.assert_awaited_once_with(operation_id="operation", max_bytes=max_bytes)
    close_session.assert_awaited_once_with(session)


@pytest.fixture
async def async_session_factory() -> AsyncIterator[Callable[[], AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def get_session() -> AsyncSession:
        return session_maker()

    yield get_session

    await engine.dispose()


async def _claim(
    repository: DurableBridgeRepository,
    *,
    instance_id: str,
    lease_ttl_seconds: float = 120.0,
    latest_turn_state: str | None = None,
    allow_takeover: bool = False,
    session_key_value: str = "sid-fence",
):
    return await repository.claim_session(
        session_key_kind="session_header",
        session_key_value=session_key_value,
        api_key_scope="__anonymous__",
        instance_id=instance_id,
        owner_process_epoch="test-process",
        lease_ttl_seconds=lease_ttl_seconds,
        account_id="acc-1",
        model="gpt-5.4",
        service_tier=None,
        latest_turn_state=latest_turn_state,
        latest_response_id=None,
        allow_takeover=allow_takeover,
    )


@pytest.mark.asyncio
async def test_stale_epoch_renewal_is_fenced_against_concurrent_takeover(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    """A fenced-out renewal must not overwrite the new owner's lease or turn state.

    Replica A's repository session keeps the pre-takeover row in its identity
    map, emulating the PostgreSQL READ COMMITTED lost-update window (and a
    stale cross-process SQLite read). The old read-check-then-write renewal
    trusted that stale row and overwrote replica B's continuity anchors.
    """

    session_a = async_session_factory()
    session_b = async_session_factory()
    try:
        repo_a = DurableBridgeRepository(session_a)
        repo_b = DurableBridgeRepository(session_b)
        claimed = await _claim(repo_a, instance_id="instance-a", latest_turn_state="turn-a")
        # Pin the pre-takeover row in replica A's identity map so a
        # read-check-then-write renewal sees the stale ownership snapshot.
        stale_row = await session_a.get(HttpBridgeSessionRecord, claimed.id)
        assert stale_row is not None
        taken_over = await _claim(
            repo_b,
            instance_id="instance-b",
            latest_turn_state="turn-b",
            allow_takeover=True,
        )
        assert taken_over.owner_instance_id == "instance-b"
        assert taken_over.owner_epoch == claimed.owner_epoch + 1

        renewed = await repo_a.renew_session(
            session_id=claimed.id,
            instance_id="instance-a",
            owner_epoch=claimed.owner_epoch,
            lease_ttl_seconds=9999.0,
            latest_turn_state="turn-a-stale",
        )

        assert renewed is not None
        assert renewed.owner_instance_id == "instance-b"
        assert renewed.owner_epoch == taken_over.owner_epoch
        assert renewed.latest_turn_state == "turn-b"

        verify_session = async_session_factory()
        try:
            row = await verify_session.get(HttpBridgeSessionRecord, claimed.id)
            assert row is not None
            assert row.owner_instance_id == "instance-b"
            assert row.owner_epoch == taken_over.owner_epoch
            assert row.latest_turn_state == "turn-b"
        finally:
            await verify_session.close()
    finally:
        await session_a.close()
        await session_b.close()


@pytest.mark.asyncio
async def test_stale_epoch_release_is_fenced_and_reports_current_owner(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session_a = async_session_factory()
    session_b = async_session_factory()
    try:
        repo_a = DurableBridgeRepository(session_a)
        repo_b = DurableBridgeRepository(session_b)
        claimed = await _claim(repo_a, instance_id="instance-a")
        taken_over = await _claim(repo_b, instance_id="instance-b", allow_takeover=True)

        released = await repo_a.release_session(
            session_id=claimed.id,
            instance_id="instance-a",
            owner_epoch=claimed.owner_epoch,
            draining=False,
        )

        assert released is not None
        assert released.owner_instance_id == "instance-b"
        assert released.owner_epoch == taken_over.owner_epoch
        assert released.state == HttpBridgeSessionState.ACTIVE

        verify_session = async_session_factory()
        try:
            row = await verify_session.get(HttpBridgeSessionRecord, claimed.id)
            assert row is not None
            assert row.owner_instance_id == "instance-b"
            assert row.state == HttpBridgeSessionState.ACTIVE
        finally:
            await verify_session.close()
    finally:
        await session_a.close()
        await session_b.close()


@pytest.mark.asyncio
async def test_owned_renewal_extends_lease_and_release_marks_draining(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claimed = await _claim(repository, instance_id="instance-a", lease_ttl_seconds=5.0)
        assert claimed.lease_expires_at is not None

        renewed = await repository.renew_session(
            session_id=claimed.id,
            instance_id="instance-a",
            owner_epoch=claimed.owner_epoch,
            lease_ttl_seconds=3600.0,
            latest_turn_state="turn-renewed",
        )
        assert renewed is not None
        assert renewed.owner_instance_id == "instance-a"
        assert renewed.owner_epoch == claimed.owner_epoch
        assert renewed.latest_turn_state == "turn-renewed"
        assert renewed.lease_expires_at is not None
        assert renewed.lease_expires_at > claimed.lease_expires_at

        released = await repository.release_session(
            session_id=claimed.id,
            instance_id="instance-a",
            owner_epoch=claimed.owner_epoch,
            draining=True,
        )
        assert released is not None
        assert released.owner_instance_id is None
        assert released.state == HttpBridgeSessionState.DRAINING
        assert released.closed_at is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_purge_abandoned_before_removes_expired_rows_and_aliases_keeps_live(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        abandoned_active = await _claim(repository, instance_id="crashed", session_key_value="sid-abandoned")
        await repository.upsert_alias(
            session_id=abandoned_active.id,
            alias_kind="turn_state",
            alias_value="turn-abandoned",
            api_key_scope="__anonymous__",
        )
        live = await _claim(
            repository,
            instance_id="alive",
            session_key_value="sid-live",
            lease_ttl_seconds=3600.0,
        )
        drained = await _claim(repository, instance_id="drained", session_key_value="sid-drained")
        await repository.release_session(
            session_id=drained.id,
            instance_id="drained",
            owner_epoch=drained.owner_epoch,
            draining=True,
        )
        recent_expired = await _claim(repository, instance_id="recent", session_key_value="sid-recent")

        long_ago = utcnow() - timedelta(hours=12)
        expired_lease = utcnow() - timedelta(hours=11)
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id.in_([abandoned_active.id, drained.id]))
            .values(last_seen_at=long_ago, lease_expires_at=expired_lease)
        )
        # Live-lease row with old activity must survive; expired lease with
        # recent activity must survive too.
        await session.execute(
            update(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.id == live.id).values(last_seen_at=long_ago)
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == recent_expired.id)
            .values(lease_expires_at=expired_lease)
        )
        await session.commit()

        deleted = await repository.purge_abandoned_before(utcnow() - timedelta(hours=1))

        assert deleted == 2
        remaining = await session.execute(select(HttpBridgeSessionRecord.id))
        remaining_ids = set(remaining.scalars().all())
        assert remaining_ids == {live.id, recent_expired.id}
        aliases = await session.execute(select(HttpBridgeSessionAlias.session_id))
        assert abandoned_active.id not in set(aliases.scalars().all())
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_get_sessions_by_ids_chunks_large_id_sets(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    """Reconciliation lookups must chunk the IN(...) clause so candidate sets
    larger than the database bind-parameter limit still resolve every row."""

    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claims = [
            await _claim(repository, instance_id=f"inst-{index}", session_key_value=f"sid-chunk-{index}")
            for index in range(5)
        ]
        candidate_ids = [claim.id for claim in claims] + [claims[0].id, "missing-session-id"]

        snapshots = await repository.get_sessions_by_ids(candidate_ids, chunk_size=2)

        assert len(snapshots) == len(claims)
        assert {snapshot.id for snapshot in snapshots} == {claim.id for claim in claims}
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_retry_circuit_drops_a_base_mismatched_concurrent_write(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # Two replicas race their first strike. The loser's write carries a base
    # that no longer matches the row, so it drops without touching the row:
    # count, cooldown, detail, and epoch all stay the winner's, keeping every
    # in-flight fence on that epoch valid. The loser reconciles from the
    # returned row and its next strike, carrying the current base, lands.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)

        for updated_at in (1000.0, 1001.0):
            await repository.upsert_retry_circuit(
                session_key_kind="session_header",
                session_key_value="sid-retry-conflict",
                api_key_scope="key-1",
                consecutive_failures=1,
                cooldown_until_epoch=0.0,
                last_detail="clean_close",
                updated_at_epoch=updated_at,
                failure_threshold=2,
                conflict_cooldown_until_epoch=2000.0,
            )

        row = await session.get(
            HttpBridgeRetryCircuit,
            (
                "session_header",
                durable_bridge_hash("sid-retry-conflict"),
                "key-1",
            ),
        )
        assert row is not None
        assert row.consecutive_failures == 1
        assert row.cooldown_until_epoch == 0.0
        assert row.updated_at_epoch == 1000.0, "a dropped write must not disturb the row's version"

        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-conflict",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=0.0,
            last_detail="clean_close",
            updated_at_epoch=1002.0,
            base_updated_at_epoch=1000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2000.0,
        )
        await session.refresh(row)
        assert row.consecutive_failures == 2
        assert row.cooldown_until_epoch >= 2000.0
        assert row.last_detail == "clean_close"
        assert row.updated_at_epoch == 1002.0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_retry_circuit_cooldown_scales_with_failure_count(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # Each write carries the exact base it loaded, so the chain lands as a
    # sequence of CAS matches and the server-side backoff schedule scales
    # with the accumulated count.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        for failures, updated_at, base_updated_at in (
            (1, 1000.0, 0.0),
            (2, 1001.0, 1000.0),
            (3, 1002.0, 1001.0),
        ):
            await repository.upsert_retry_circuit(
                session_key_kind="session_header",
                session_key_value="sid-retry-backoff-conflict",
                api_key_scope="key-1",
                consecutive_failures=failures,
                cooldown_until_epoch=0.0,
                last_detail="stream_incomplete",
                updated_at_epoch=updated_at,
                base_updated_at_epoch=base_updated_at,
                failure_threshold=2,
                conflict_cooldown_until_epoch=updated_at + 60.0,
            )

        row = await session.get(
            HttpBridgeRetryCircuit,
            (
                "session_header",
                durable_bridge_hash("sid-retry-backoff-conflict"),
                "key-1",
            ),
        )
        assert row is not None
        assert row.consecutive_failures == 3
        assert row.cooldown_until_epoch >= 1122.0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_retry_circuit_ignores_out_of_order_failure_snapshot(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        for failures, updated_at, base_updated_at in (
            (1, 1000.0, 0.0),
            (2, 1002.0, 1000.0),
            (1, 1001.0, 1002.0),
        ):
            await repository.upsert_retry_circuit(
                session_key_kind="session_header",
                session_key_value="sid-retry-out-of-order",
                api_key_scope="key-1",
                consecutive_failures=failures,
                cooldown_until_epoch=0.0,
                last_detail="stream_incomplete",
                updated_at_epoch=updated_at,
                base_updated_at_epoch=base_updated_at,
                failure_threshold=2,
                conflict_cooldown_until_epoch=updated_at + 60.0,
            )

        row = await session.get(
            HttpBridgeRetryCircuit,
            (
                "session_header",
                durable_bridge_hash("sid-retry-out-of-order"),
                "key-1",
            ),
        )
        assert row is not None
        assert row.consecutive_failures == 2
        assert row.updated_at_epoch == 1002.0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_retry_circuit_merges_lagging_wall_clock_failure_from_loaded_base(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-clock-skew",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )

        # This replica loaded the row at 2000, then its wall clock lagged the
        # writer that persisted it. The unchanged loaded row is a CAS match,
        # so the failure must still open the shared circuit.
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-clock-skew",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=1500.0,
            base_updated_at_epoch=2000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=1560.0,
        )

        row = await session.get(
            HttpBridgeRetryCircuit,
            (
                "session_header",
                durable_bridge_hash("sid-retry-clock-skew"),
                "key-1",
            ),
        )
        assert row is not None
        assert row.consecutive_failures == 2
        assert row.cooldown_until_epoch >= 2060.0
        assert row.updated_at_epoch == 2000.0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_scheduled_purge_spares_recently_claimed_generations(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # A replay claim advances only the admission generation and leaves the
    # timestamp untouched, so a claimed row near the TTL could be reaped
    # while its replay was still in flight. Ever-claimed rows get one extra
    # TTL of grace; unclaimed stale rows and long-dead claimed rows are
    # still reaped.
    from app.modules.proxy.durable_bridge_repository import DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS

    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        cutoff = 100000.0
        for value, epoch, admission in (
            ("sid-purge-unclaimed-stale", cutoff - 10.0, 0),
            ("sid-purge-claimed-recent", cutoff - 10.0, 3),
            ("sid-purge-claimed-ancient", cutoff - DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS - 10.0, 3),
        ):
            await repository.upsert_retry_circuit(
                session_key_kind="session_header",
                session_key_value=value,
                api_key_scope="key-1",
                consecutive_failures=2,
                cooldown_until_epoch=0.0,
                last_detail="stream_incomplete",
                updated_at_epoch=epoch,
                base_updated_at_epoch=0.0,
                failure_threshold=2,
                conflict_cooldown_until_epoch=epoch + 60.0,
            )
            if admission:
                await session.execute(
                    update(HttpBridgeRetryCircuit)
                    .where(HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash(value))
                    .values(admission_generation=admission)
                )
                await session.commit()

        deleted = await repository.purge_retry_circuits_before(cutoff)

        assert deleted == 2, "the unclaimed stale row and the ancient claimed row are reaped"
        surviving = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-purge-claimed-recent"), "key-1"),
        )
        assert surviving is not None, "a recently claimed generation survives one extra TTL"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_scheduled_purge_keeps_tombstones_until_bridge_retention(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # An anchor_abandoned tombstone guards continuity that outlives the
    # circuit TTL; reaping it on the circuit schedule would let the
    # unanchored-delta gate dispatch a context-free request. It falls only
    # to the caller's bridge-retention cutoff.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        cutoff = 100000.0
        for value, epoch in (
            ("sid-tombstone-recent", cutoff - 10.0),
            ("sid-tombstone-ancient", cutoff - 90000.0),
        ):
            await repository.upsert_retry_circuit(
                session_key_kind="session_header",
                session_key_value=value,
                api_key_scope="key-1",
                consecutive_failures=2,
                cooldown_until_epoch=0.0,
                last_detail="stream_incomplete",
                updated_at_epoch=epoch,
                base_updated_at_epoch=0.0,
                failure_threshold=2,
                conflict_cooldown_until_epoch=epoch + 60.0,
            )
            await session.execute(
                update(HttpBridgeRetryCircuit)
                .where(HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash(value))
                .values(consecutive_failures=0, last_detail="anchor_abandoned")
            )
            await session.commit()

        deleted = await repository.purge_retry_circuits_before(cutoff)
        assert deleted == 0, "without a retention cutoff every tombstone survives the circuit TTL"

        deleted = await repository.purge_retry_circuits_before(cutoff, tombstone_cutoff_epoch=cutoff - 80000.0)
        assert deleted == 1, "only the tombstone past the bridge-retention cutoff is reaped"
        surviving = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-tombstone-recent"), "key-1"),
        )
        assert surviving is not None
        assert surviving.last_detail == "anchor_abandoned"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_tombstone_outlives_its_still_live_session(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # A crash between a poison settle and its registration leaves a live
    # session storing the poisoned continuity while delta requests keep
    # its lease fresh; the tombstone's fixed epoch would age past the
    # retention cutoff and an age-only reap would hand the next request
    # the old anchor. The tombstone falls only when no session still
    # resolves the key with continuity.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claimed = await _claim(
            repository,
            instance_id="alive",
            session_key_value="sid-tombstone-live-session",
            latest_turn_state="turn-live",
        )
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-tombstone-live-session",
            api_key_scope="__anonymous__",
            consecutive_failures=2,
            cooldown_until_epoch=2600.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )
        await session.execute(
            update(HttpBridgeRetryCircuit)
            .where(HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash("sid-tombstone-live-session"))
            .values(consecutive_failures=0, last_detail="anchor_abandoned")
        )
        await session.commit()

        deleted = await repository.purge_retry_circuits_before(100000.0, tombstone_cutoff_epoch=90000.0)
        assert deleted == 0, "a tombstone whose session still stores continuity survives the age cutoff"

        cleared = await repository.rebind_session_account(
            session_id=claimed.id,
            instance_id="alive",
            owner_epoch=claimed.owner_epoch,
            account_id="acc-1",
            clear_continuity=True,
        )
        assert cleared

        deleted = await repository.purge_retry_circuits_before(100000.0, tombstone_cutoff_epoch=90000.0)
        assert deleted == 1, "the tombstone falls once its session's continuity is gone"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_lagging_clock_strike_fences_the_episode_reset(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # A cross-replica lagging-clock strike merges a higher count without
    # moving the epoch; a reset fenced only on epoch and admission
    # generation would still match and zero the newer episode's cooldown.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-reset-count-fence",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=2600.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )
        # Lagging clock: the merge lands a third strike without advancing
        # the epoch past the observed 2000.0.
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-reset-count-fence",
            api_key_scope="key-1",
            consecutive_failures=3,
            cooldown_until_epoch=2700.0,
            last_detail="stream_incomplete",
            updated_at_epoch=1990.0,
            base_updated_at_epoch=2000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2160.0,
        )

        cleared = await repository.delete_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-reset-count-fence",
            api_key_scope="key-1",
            expected_updated_at_epoch=2000.0,
            expected_admission_generation=0,
            expected_consecutive_failures=2,
        )

        assert cleared is False, "the count fence must see the lagging-clock strike the epoch cannot"
        row = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-reset-count-fence"), "key-1"),
        )
        assert row is not None
        assert row.consecutive_failures == 3, "the newer episode's count and cooldown survive"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_detail_only_tombstone_transition_fences_the_stale_purge(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # The transitional tombstone supersede deliberately moves neither the
    # timestamp nor the admission generation; a stale purge fenced only on
    # those would delete the newly installed crash-safety fence and let
    # the caller revoke quarantine while the old poisoned anchor is still
    # the stored one. The purge fences on the observed count and detail.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-purge-detail-fence",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=2600.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )
        assert await repository.supersede_retry_circuit_detail(
            session_key_kind="session_header",
            session_key_value="sid-purge-detail-fence",
            api_key_scope="key-1",
            expected_updated_at_epoch=2000.0,
            expected_consecutive_failures=2,
            expected_last_detail="stream_incomplete",
            last_detail="anchor_abandoned",
        )

        purged = await repository.purge_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-purge-detail-fence",
            api_key_scope="key-1",
            expected_updated_at_epoch=2000.0,
            expected_admission_generation=0,
            expected_consecutive_failures=2,
            fence_last_detail=True,
            expected_last_detail="stream_incomplete",
        )

        assert purged is False, "the observed-detail fence must see the detail-only tombstone transition"
        row = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-purge-detail-fence"), "key-1"),
        )
        assert row is not None
        assert row.last_detail == "anchor_abandoned", "the crash-safety fence survives the missed purge"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_strike_cannot_overwrite_an_abandonment_tombstone(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # The tombstone on a settled abandonment is what fails anchorless
    # deltas closed on every replica; a subsequent unanchored failure
    # merging onto the row must count its strike without erasing it, or a
    # below-threshold clean failure would leave neither poison evidence
    # nor the tombstone and a delta-only continuation would silently start
    # a new conversation.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-tombstone",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=2600.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )
        await session.execute(
            update(HttpBridgeRetryCircuit)
            .where(HttpBridgeRetryCircuit.session_key_hash == durable_bridge_hash("sid-sticky-tombstone"))
            .values(consecutive_failures=0, last_detail="anchor_abandoned")
        )
        await session.commit()

        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-tombstone",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="clean_close",
            updated_at_epoch=2100.0,
            base_updated_at_epoch=2000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2160.0,
        )
        row = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-sticky-tombstone"), "key-1"),
        )
        assert row is not None
        assert row.consecutive_failures == 1, "the strike still counts"
        assert row.last_detail == "anchor_abandoned", (
            "the abandonment tombstone is sticky until fresh continuity is established"
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_one_failure_poison_detail_is_sticky_at_a_threshold_of_one(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # With the anchor-poison threshold configured to one, the first poison
    # strike already authorizes the abandonment and its failed clear owes
    # the debt from a one-failure row; the sticky predicate must fence at
    # that effective threshold, not the circuit threshold of two.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-threshold-one",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            poison_sticky_threshold=1,
            conflict_cooldown_until_epoch=2060.0,
        )
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-threshold-one",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=2600.0,
            last_detail="clean_close",
            updated_at_epoch=2100.0,
            base_updated_at_epoch=2000.0,
            failure_threshold=2,
            poison_sticky_threshold=1,
            conflict_cooldown_until_epoch=2160.0,
        )
        row = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-sticky-threshold-one"), "key-1"),
        )
        assert row is not None
        assert row.consecutive_failures == 2, "the clean strike still counts"
        assert row.last_detail == "stream_incomplete", (
            "a one-failure poison detail is sticky at the effective threshold of one"
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_clean_strike_cannot_overwrite_an_at_threshold_poison_detail(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # The at-threshold poison detail is the cross-replica record that the
    # anchor's clear is still owed; a clean probe failure merging onto the
    # row must count its strike without erasing that record. A poison-class
    # strike may still update it, and a below-threshold row stays freely
    # overwritable.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-poison",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=2600.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-poison",
            api_key_scope="key-1",
            consecutive_failures=3,
            cooldown_until_epoch=2600.0,
            last_detail="clean_close",
            updated_at_epoch=2100.0,
            base_updated_at_epoch=2000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2160.0,
        )
        row = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-sticky-poison"), "key-1"),
        )
        assert row is not None
        assert row.consecutive_failures == 3, "the clean strike still counts"
        assert row.last_detail == "stream_incomplete", (
            "an at-threshold poison detail is sticky against non-poison strikes"
        )

        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-sticky-poison",
            api_key_scope="key-1",
            consecutive_failures=4,
            cooldown_until_epoch=2700.0,
            last_detail="stream_idle_timeout",
            updated_at_epoch=2200.0,
            base_updated_at_epoch=row.updated_at_epoch,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2260.0,
        )
        await session.refresh(row)
        assert row.last_detail == "stream_idle_timeout", "a poison-class strike may still update the detail"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_supersession_is_fenced_against_lagging_clock_strikes(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # A lagging-clock strike merges onto the row without moving its version
    # (greatest keeps the unchanged epoch) while incrementing the count and
    # writing its poison detail. A supersession fenced on the epoch alone
    # would then overwrite that fresh evidence with ``anchor_superseded``;
    # the count in the fence is what makes the strike outrank it.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-supersede-fence",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=0.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=2060.0,
        )
        # The lagging-clock strike: count 1 -> 2, epoch stays 2000.
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-supersede-fence",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=1500.0,
            base_updated_at_epoch=2000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=1560.0,
        )

        stale_supersession = await repository.supersede_retry_circuit_detail(
            session_key_kind="session_header",
            session_key_value="sid-supersede-fence",
            api_key_scope="key-1",
            expected_updated_at_epoch=2000.0,
            expected_consecutive_failures=1,
            expected_last_detail="stream_incomplete",
            last_detail="anchor_superseded",
        )
        assert stale_supersession is False, "a supersession behind a lagging-clock strike must miss its fence"
        row = await session.get(
            HttpBridgeRetryCircuit,
            ("session_header", durable_bridge_hash("sid-supersede-fence"), "key-1"),
        )
        assert row is not None
        assert row.last_detail == "stream_incomplete", "the concurrent strike's poison class must survive"

        current_supersession = await repository.supersede_retry_circuit_detail(
            session_key_kind="session_header",
            session_key_value="sid-supersede-fence",
            api_key_scope="key-1",
            expected_updated_at_epoch=2000.0,
            expected_consecutive_failures=2,
            expected_last_detail="stream_incomplete",
            last_detail="anchor_superseded",
        )
        assert current_supersession is True
        # A second completion's forward supersession finds the sentinel
        # already in place and must not claim ownership of it.
        double_supersession = await repository.supersede_retry_circuit_detail(
            session_key_kind="session_header",
            session_key_value="sid-supersede-fence",
            api_key_scope="key-1",
            expected_updated_at_epoch=2000.0,
            expected_consecutive_failures=2,
            expected_last_detail="stream_incomplete",
            last_detail="anchor_superseded",
        )
        assert double_supersession is False, "only one completion owns the supersession of a shared row"
        current_supersession = True
        assert current_supersession is True
        await session.refresh(row)
        assert row.last_detail == "anchor_superseded"
        assert row.consecutive_failures == 2, "the supersession never charges a failure"
        assert row.updated_at_epoch == 2000.0, "the supersession never moves the version"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_a_stale_strike_after_the_reset_row_is_restruck_is_dropped(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    # Worker A loads an old episode, worker B resets it, worker C records the
    # new lineage's first failure, and only then does A's delayed write
    # arrive. A's base matches neither the reset row nor C's re-struck row,
    # so it must drop entirely; merging it would open a false cooldown on a
    # lineage that has seen one real failure and could abandon the fresh
    # anchor that ended A's episode.
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-restruck-stale",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=1000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=1060.0,
        )
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-restruck-stale",
            api_key_scope="key-1",
            consecutive_failures=2,
            cooldown_until_epoch=1061.0,
            last_detail="stream_incomplete",
            updated_at_epoch=1001.0,
            base_updated_at_epoch=1000.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=1061.0,
        )
        await repository.delete_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-restruck-stale",
            api_key_scope="key-1",
        )
        reset_row = await session.get(
            HttpBridgeRetryCircuit,
            (
                "session_header",
                durable_bridge_hash("sid-retry-restruck-stale"),
                "key-1",
            ),
        )
        assert reset_row is not None
        assert reset_row.consecutive_failures == 0
        reset_epoch = reset_row.updated_at_epoch

        restrike_epoch = reset_epoch + 1.0
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-restruck-stale",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=restrike_epoch,
            base_updated_at_epoch=reset_epoch,
            failure_threshold=2,
            conflict_cooldown_until_epoch=restrike_epoch + 60.0,
        )
        # A's delayed write: base predates the reset, count carries the ended
        # episode plus one more strike.
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-restruck-stale",
            api_key_scope="key-1",
            consecutive_failures=3,
            cooldown_until_epoch=restrike_epoch + 240.0,
            last_detail="stream_incomplete",
            updated_at_epoch=restrike_epoch + 2.0,
            base_updated_at_epoch=1001.0,
            failure_threshold=2,
            conflict_cooldown_until_epoch=restrike_epoch + 240.0,
        )

        await session.refresh(reset_row)
        assert reset_row.consecutive_failures == 1, (
            "a stale write must stay rejectable after the reset row is re-struck;"
            " merging it opens a false cooldown on the fresh lineage"
        )
        assert reset_row.cooldown_until_epoch == 0.0
        assert reset_row.updated_at_epoch == restrike_epoch, "a dropped write must not disturb the row's version fence"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_retry_circuit_reset_starts_new_failure_lineage(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-reset-lineage",
            api_key_scope="key-1",
            consecutive_failures=3,
            cooldown_until_epoch=2000.0,
            last_detail="stream_incomplete",
            updated_at_epoch=1000.0,
        )

        await repository.delete_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-reset-lineage",
            api_key_scope="key-1",
        )
        # A strike whose base predates the reset belongs to the settled
        # lineage and is dropped outright; the reset row stays untouched.
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-reset-lineage",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=5000.0,
            last_detail="stream_incomplete",
            updated_at_epoch=2000.0,
            base_updated_at_epoch=1000.0,
        )

        row = await session.get(
            HttpBridgeRetryCircuit,
            (
                "session_header",
                durable_bridge_hash("sid-retry-reset-lineage"),
                "key-1",
            ),
        )
        assert row is not None
        assert row.cooldown_until_epoch == 0.0
        assert row.consecutive_failures == 0, "a stale-base strike must not rebase into the reset lineage"
        assert row.last_detail is None
        reset_epoch = row.updated_at_epoch

        # A fresh strike loads the reset row first and carries its base, so
        # it lands as the first failure of the new lineage.
        await repository.upsert_retry_circuit(
            session_key_kind="session_header",
            session_key_value="sid-retry-reset-lineage",
            api_key_scope="key-1",
            consecutive_failures=1,
            cooldown_until_epoch=0.0,
            last_detail="stream_incomplete",
            updated_at_epoch=reset_epoch + 1.0,
            base_updated_at_epoch=reset_epoch,
        )
        await session.refresh(row)
        assert row.consecutive_failures == 1
        assert row.last_detail == "stream_incomplete"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_recovery_attempt_pre_dispatch_claim_can_be_rolled_back(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-recovery-rollback",
            session_key_value="sid-recovery-rollback",
        )
        attempt = await repository.record_recovery_attempt(
            session_id=claim.id,
            instance_id="inst-recovery-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint="fingerprint-recovery-rollback",
            request_id="request-recovery-rollback",
            account_id=None,
            model="gpt-5.4",
            replay_safe=True,
        )
        assert attempt is not None
        assert await repository.mark_recovery_attempt_replayed(
            session_id=claim.id,
            instance_id="inst-recovery-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint="fingerprint-recovery-rollback",
        )
        assert await repository.rollback_recovery_attempt_replayed(
            session_id=claim.id,
            instance_id="inst-recovery-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint="fingerprint-recovery-rollback",
        )
        restored = await repository.lookup_recovery_attempt(
            session_id=claim.id,
            request_fingerprint="fingerprint-recovery-rollback",
        )
        assert restored is not None
        assert restored.state.value == "unknown"
        assert await repository.rollback_recovery_attempt_before_dispatch(
            session_id=claim.id,
            instance_id="inst-recovery-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint="fingerprint-recovery-rollback",
        )
        assert (
            await repository.lookup_recovery_attempt(
                session_id=claim.id,
                request_fingerprint="fingerprint-recovery-rollback",
            )
            is None
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_operation_ledger_is_fenced_and_idempotent(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-operation-ledger", session_key_value="sid-operation")
        fingerprint = durable_bridge_hash("continuation-body")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        created = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-ledger",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
            request_text='{"model":"gpt-5.6","input":"turn"}',
        )
        assert created is not None
        assert created.created is True
        assert created.state == "submitted"
        assert created.request_text == '{"model":"gpt-5.6","input":"turn"}'
        assert created.event_spool_complete is False
        operation_row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert operation_row is not None
        assert operation_row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1

        existing = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-ledger",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert existing is not None
        assert existing.created is False
        assert existing.operation_id == operation_id

        assert await repository.update_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-ledger",
            owner_epoch=claim.owner_epoch,
            state="completed",
            response_id="resp-completed",
        )
        completed = await repository.get_latest_completed_operation(
            session_id=claim.id,
            parent_response_id="resp-parent",
        )
        assert completed is not None
        assert completed.response_id == "resp-completed"
        by_fingerprint = await repository.get_operation_by_fingerprint(request_fingerprint=fingerprint)
        assert by_fingerprint is not None
        assert by_fingerprint.operation_id == operation_id
        cross_session_completed = await repository.get_latest_completed_operation_any_session(
            parent_response_id="resp-parent",
        )
        assert cross_session_completed is not None
        assert cross_session_completed.response_id == "resp-completed"

        assert await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-ledger",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.completed"}\n\n',
            max_bytes=1024,
        )
        # Repeated identical SSE blocks are distinct downstream occurrences,
        # so replay must preserve both copies rather than hash-deduplicating.
        assert await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-ledger",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.completed"}\n\n',
            max_bytes=1024,
        )
        assert await repository.get_operation_events(operation_id=operation_id) == [
            'data: {"type":"response.completed"}\n\n',
            'data: {"type":"response.completed"}\n\n',
        ]
        # A missing parent turn makes the chain ineligible rather than
        # silently constructing an incomplete conversation.
        assert await repository.get_replayable_transcript(response_id="resp-completed") is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chunk_operation_replays_exact_events(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-replay", session_key_value="sid-chunk-replay")
        fingerprint = durable_bridge_hash("chunk-replay")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-replay",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
            request_text='{"model":"gpt-5.6","input":"turn"}',
        )
        assert operation is not None
        first_events = (
            'data: {"type":"response.created"}\n\n',
            'data: {"type":"response.output_text.delta","delta":"안녕"}\n\n',
        )
        terminal_events = (
            'data: {"type":"response.completed"}\n\n',
            'data: {"type":"response.completed"}\n\n',
        )
        first_chunk = encode_durable_bridge_transcript_chunk(first_events)
        terminal_chunk = encode_durable_bridge_transcript_chunk(terminal_events)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(
                spool_format=HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
                state="completed",
                response_id="resp-chunk-completed",
                event_spool_complete=True,
                event_bytes=sum(len(event.encode("utf-8")) for event in first_events + terminal_events),
            )
        )
        session.add_all(
            [
                HttpBridgeOperationEventChunk(
                    operation_id=operation_id,
                    first_sequence_number=1,
                    event_count=first_chunk.event_count,
                    codec=first_chunk.codec,
                    uncompressed_bytes=first_chunk.uncompressed_bytes,
                    payload=first_chunk.payload,
                    payload_sha256=first_chunk.payload_sha256,
                ),
                HttpBridgeOperationEventChunk(
                    operation_id=operation_id,
                    first_sequence_number=3,
                    event_count=terminal_chunk.event_count,
                    codec=terminal_chunk.codec,
                    uncompressed_bytes=terminal_chunk.uncompressed_bytes,
                    payload=terminal_chunk.payload,
                    payload_sha256=terminal_chunk.payload_sha256,
                ),
            ]
        )
        await session.commit()

        expected = list(first_events + terminal_events)
        assert await repository.get_operation_events(operation_id=operation_id) == expected
        transcript = await repository.get_replayable_transcript(response_id="resp-chunk-completed")
        assert transcript is not None
        assert len(transcript) == 1
        assert transcript[0].events == tuple(expected)

        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(event_bytes=sum(len(event.encode("utf-8")) for event in expected) - 1)
        )
        await session.commit()
        assert await repository.get_operation_events(operation_id=operation_id) == []
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("table_name", "column_name", "value"),
    [
        ("http_bridge_operations", "event_bytes", 1.5),
        ("http_bridge_operation_event_chunks", "event_count", "not-an-integer"),
        ("http_bridge_operation_event_chunks", "payload", "not-binary"),
    ],
)
async def test_chunk_operation_rejects_malformed_persisted_metadata(
    async_session_factory: Callable[[], AsyncSession],
    table_name: str,
    column_name: str,
    value: object,
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-metadata", session_key_value="sid-chunk-metadata")
        fingerprint = durable_bridge_hash(f"chunk-metadata:{table_name}:{column_name}")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-metadata",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
            request_text='{"input":"turn"}',
        )
        encoded = encode_durable_bridge_transcript_chunk(("a",))
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(
                spool_format=HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
                event_bytes=1,
            )
        )
        session.add(
            HttpBridgeOperationEventChunk(
                operation_id=operation_id,
                first_sequence_number=1,
                event_count=encoded.event_count,
                codec=encoded.codec,
                uncompressed_bytes=encoded.uncompressed_bytes,
                payload=encoded.payload,
                payload_sha256=encoded.payload_sha256,
            )
        )
        await session.commit()
        await session.execute(
            text(f"UPDATE {table_name} SET {column_name} = :value WHERE operation_id = :operation_id"),
            {"value": value, "operation_id": operation_id},
        )
        await session.commit()

        assert await repository.get_operation_events(operation_id=operation_id) == []
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_next_operation_chunk_sequence_selects_only_metadata() -> None:
    result = SimpleNamespace(one_or_none=lambda: (4, 3))
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    repository = DurableBridgeRepository(cast(AsyncSession, session))

    assert await repository._next_operation_chunk_sequence("operation") == 7

    statement = session.execute.call_args.args[0]
    assert tuple(statement.selected_columns.keys()) == ("first_sequence_number", "event_count")


@pytest.mark.asyncio
async def test_chunk_writer_persists_batch_and_terminal_atomically(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = async_session_factory()
    try:

        async def encode_off_loop(function, *args):
            return function(*args)

        encode = AsyncMock(side_effect=encode_off_loop)
        monkeypatch.setattr(durable_repo_module.asyncio, "to_thread", encode)
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-writer", session_key_value="sid-chunk-writer")
        fingerprint = durable_bridge_hash("chunk-writer")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-writer",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
            request_text='{"input":"turn"}',
        )
        first_events = ("created", "delta")
        assert await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="inst-chunk-writer",
                    owner_epoch=claim.owner_epoch,
                    event_text=event,
                )
                for event in first_events
            ],
            max_bytes=1024,
        )
        assert await repository.append_terminal_operation_chunk(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-writer",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.completed"}\n\n',
            max_bytes=1024,
            state="completed",
            response_id="resp-chunk-writer",
        )

        row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert row is not None
        assert row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2
        assert row.state == "completed"
        assert row.response_id == "resp-chunk-writer"
        assert row.event_spool_complete is True
        assert (
            await session.scalar(
                select(HttpBridgeOperationEvent).where(HttpBridgeOperationEvent.operation_id == operation_id)
            )
            is None
        )
        chunks = (
            (
                await session.execute(
                    select(HttpBridgeOperationEventChunk)
                    .where(HttpBridgeOperationEventChunk.operation_id == operation_id)
                    .order_by(HttpBridgeOperationEventChunk.first_sequence_number)
                )
            )
            .scalars()
            .all()
        )
        assert [(chunk.first_sequence_number, chunk.event_count) for chunk in chunks] == [(1, 2), (3, 1)]
        assert await repository.get_operation_events(operation_id=operation_id) == [
            *first_events,
            'data: {"type":"response.completed"}\n\n',
        ]
        assert await repository.get_replayable_transcript(response_id="resp-chunk-writer") is not None
        assert encode.await_count == 2
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chunk_writer_refuses_mixed_legacy_material(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-conflict", session_key_value="sid-chunk-conflict")
        fingerprint = durable_bridge_hash("chunk-conflict")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-conflict",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        assert await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-conflict",
            owner_epoch=claim.owner_epoch,
            event_text="legacy",
            max_bytes=1024,
        )

        assert not await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="inst-chunk-conflict",
                    owner_epoch=claim.owner_epoch,
                    event_text="chunk",
                )
            ],
            max_bytes=1024,
        )
        assert not await repository.append_terminal_operation_chunk(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-conflict",
            owner_epoch=claim.owner_epoch,
            event_text="terminal",
            max_bytes=1024,
            state="failed",
            response_id="resp-conflict",
        )
        row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert row is not None
        await session.refresh(row)
        assert row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
        assert row.state == "failed"
        assert row.response_id == "resp-conflict"
        assert row.event_spool_complete is False
        assert (
            await session.scalar(
                select(HttpBridgeOperationEventChunk).where(HttpBridgeOperationEventChunk.operation_id == operation_id)
            )
            is None
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_oversized_terminal_chunk_settles_incomplete_operation(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-oversize", session_key_value="sid-chunk-oversize")
        fingerprint = durable_bridge_hash("chunk-oversize")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-oversize",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        assert not await repository.append_terminal_operation_chunk(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-oversize",
            owner_epoch=claim.owner_epoch,
            event_text="too-large",
            max_bytes=3,
            state="failed",
            response_id="resp-oversized",
        )

        row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert row is not None
        assert row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
        assert row.state == "failed"
        assert row.response_id == "resp-oversized"
        assert row.event_spool_complete is False
        assert (
            await session.scalar(
                select(HttpBridgeOperationEventChunk).where(HttpBridgeOperationEventChunk.operation_id == operation_id)
            )
            is None
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chunk_writer_enforces_reader_event_count_limit(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = async_session_factory()
    try:
        monkeypatch.setattr(durable_repo_module, "DURABLE_BRIDGE_TRANSCRIPT_MAX_EVENTS", 2)
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-count", session_key_value="sid-chunk-count")
        fingerprint = durable_bridge_hash("chunk-count")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-count",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        assert await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="inst-chunk-count",
                    owner_epoch=claim.owner_epoch,
                    event_text=event,
                )
                for event in ("one", "two")
            ],
            max_bytes=1024,
        )
        assert not await repository.append_terminal_operation_chunk(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-count",
            owner_epoch=claim.owner_epoch,
            event_text="terminal",
            max_bytes=1024,
            state="failed",
            response_id="resp-count-limit",
        )
        row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert row is not None
        assert row.state == "failed"
        assert row.event_spool_complete is False
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chunk_writer_rejects_before_compression(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-precompress", session_key_value="sid-precompress")
        fingerprint = durable_bridge_hash("precompress")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-precompress",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        encoder = MagicMock(side_effect=AssertionError("compression should not run"))
        monkeypatch.setattr(durable_repo_module, "encode_durable_bridge_transcript_chunk", encoder)

        assert not await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="inst-precompress",
                    owner_epoch=claim.owner_epoch,
                    event_text="oversized",
                )
            ],
            max_bytes=1,
        )
        assert not await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="wrong-owner",
                    owner_epoch=claim.owner_epoch,
                    event_text="small",
                )
            ],
            max_bytes=1024,
        )
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(event_bytes=1024)
        )
        await session.commit()
        assert not await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="inst-precompress",
                    owner_epoch=claim.owner_epoch,
                    event_text="small",
                )
            ],
            max_bytes=1024,
        )
        encoder.assert_not_called()
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("first_sequence_number,payload_sha256", [(2, None), (1, "0" * 64)])
async def test_chunk_operation_rejects_sequence_gap_or_corruption(
    async_session_factory: Callable[[], AsyncSession],
    first_sequence_number: int,
    payload_sha256: str | None,
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-invalid", session_key_value="sid-chunk-invalid")
        fingerprint = durable_bridge_hash(f"chunk-invalid:{first_sequence_number}:{payload_sha256}")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-invalid",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
            request_text='{"input":"turn"}',
        )
        encoded = encode_durable_bridge_transcript_chunk(('data: {"type":"response.completed"}\n\n',))
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(
                spool_format=HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
                state="completed",
                response_id=f"resp-{operation_id}",
                event_spool_complete=True,
            )
        )
        session.add(
            HttpBridgeOperationEventChunk(
                operation_id=operation_id,
                first_sequence_number=first_sequence_number,
                event_count=encoded.event_count,
                codec=encoded.codec,
                uncompressed_bytes=encoded.uncompressed_bytes,
                payload=encoded.payload,
                payload_sha256=payload_sha256 or encoded.payload_sha256,
            )
        )
        await session.commit()

        assert await repository.get_operation_events(operation_id=operation_id) == []
        assert await repository.get_replayable_transcript(response_id=f"resp-{operation_id}") is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chunk_spool_blocks_rollback_and_is_cleared_by_reset(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-chunk-reset", session_key_value="sid-chunk-reset")
        fingerprint = durable_bridge_hash("chunk-reset")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-reset",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        encoded = encode_durable_bridge_transcript_chunk(("event",))
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(spool_format=HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2)
        )
        await session.commit()
        assert not await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-reset",
            owner_epoch=claim.owner_epoch,
            event_text="must-not-mix-formats",
            max_bytes=1024,
        )
        session.add(
            HttpBridgeOperationEventChunk(
                operation_id=operation_id,
                first_sequence_number=1,
                event_count=encoded.event_count,
                codec=encoded.codec,
                uncompressed_bytes=encoded.uncompressed_bytes,
                payload=encoded.payload,
                payload_sha256=encoded.payload_sha256,
            )
        )
        await session.commit()

        assert not await repository.rollback_operation_before_dispatch(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-reset",
            owner_epoch=claim.owner_epoch,
        )
        assert await repository.reset_operation_event_spool(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-reset",
            owner_epoch=claim.owner_epoch,
        )
        assert (
            await session.scalar(
                select(HttpBridgeOperationEventChunk).where(HttpBridgeOperationEventChunk.operation_id == operation_id)
            )
            is None
        )
        reset_row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert reset_row is not None
        assert reset_row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_durable_bridge_presence_query_includes_chunk_table(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        assert await missing_durable_bridge_tables(session) == ()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_chunk_format_resets_on_failed_rebind_and_unknown_claim(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-format-reset", session_key_value="sid-format-reset")
        encoded = encode_durable_bridge_transcript_chunk(("event",))

        async def seed(operation_id: str, fingerprint: str, state: str) -> None:
            assert await repository.record_operation(
                operation_id=operation_id,
                session_id=claim.id,
                instance_id="inst-format-reset",
                owner_epoch=claim.owner_epoch,
                request_fingerprint=fingerprint,
                account_id="account-operation",
                model="gpt-5.6",
                parent_response_id=None,
            )
            await session.execute(
                update(HttpBridgeOperationRecord)
                .where(HttpBridgeOperationRecord.operation_id == operation_id)
                .values(
                    state=state,
                    spool_format=HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
                    event_bytes=len("event"),
                )
            )
            session.add(
                HttpBridgeOperationEventChunk(
                    operation_id=operation_id,
                    first_sequence_number=1,
                    event_count=encoded.event_count,
                    codec=encoded.codec,
                    uncompressed_bytes=encoded.uncompressed_bytes,
                    payload=encoded.payload,
                    payload_sha256=encoded.payload_sha256,
                )
            )
            await session.commit()

        failed_fingerprint = durable_bridge_hash("failed-format-reset")
        failed_operation_id = durable_bridge_operation_id(claim.id, failed_fingerprint)
        await seed(failed_operation_id, failed_fingerprint, "failed")
        rebound = await repository.record_operation(
            operation_id=failed_operation_id,
            session_id=claim.id,
            instance_id="inst-format-reset",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=failed_fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        assert rebound is not None and rebound.rebound is True
        failed_row = await session.get(HttpBridgeOperationRecord, failed_operation_id)
        assert failed_row is not None
        assert failed_row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
        assert failed_row.event_bytes == 0

        unknown_fingerprint = durable_bridge_hash("unknown-format-reset")
        unknown_operation_id = durable_bridge_operation_id(claim.id, unknown_fingerprint)
        await seed(unknown_operation_id, unknown_fingerprint, "unknown")
        assert await repository.claim_unknown_operation_for_recovery(
            operation_id=unknown_operation_id,
            session_id=claim.id,
            instance_id="inst-format-reset",
            owner_epoch=claim.owner_epoch,
        )
        unknown_row = await session.get(HttpBridgeOperationRecord, unknown_operation_id)
        assert unknown_row is not None
        await session.refresh(unknown_row)
        assert unknown_row.spool_format == HTTP_BRIDGE_SPOOL_FORMAT_ROWS_V1
        assert unknown_row.event_bytes == 0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_operation_retry_reset_clears_partial_spool(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-operation-reset", session_key_value="sid-operation-reset")
        fingerprint = durable_bridge_hash("continuation-reset")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-reset",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-reset",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.output_text.delta"}\n\n',
            max_bytes=1024,
        )
        assert await repository.reset_operation_event_spool(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-reset",
            owner_epoch=claim.owner_epoch,
        )
        assert await repository.get_operation_events(operation_id=operation_id) == []
        reset = await repository.get_operation(operation_id=operation_id)
        assert reset is not None
        assert reset.event_spool_complete is False
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_terminal_operation_event_exposes_failure_after_spooling(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-terminal-event", session_key_value="sid-terminal-event")
        fingerprint = durable_bridge_hash("terminal-event")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-event",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-terminal-event",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        event_text = 'data: {"type":"response.failed"}\n\n'

        assert await repository.append_terminal_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-event",
            owner_epoch=claim.owner_epoch,
            event_text=event_text,
            max_bytes=1024,
            state="failed",
        )
        failed = await repository.get_operation(operation_id=operation_id)
        assert failed is not None
        assert failed.state == "failed"
        assert await repository.get_operation_events(operation_id=operation_id) == [event_text]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_failed_operation_rebind_rollback_restores_row_instead_of_deleting(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-rebind-rollback", session_key_value="sid-rebind-rollback")
        fingerprint = durable_bridge_hash("rebind-rollback")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-rebind-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-rebind-rollback",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.append_terminal_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-rebind-rollback",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.failed"}\n\n',
            max_bytes=1024,
            state="failed",
        )

        rebound = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-rebind-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-rebind-rollback",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert rebound is not None
        assert rebound.created is False
        assert rebound.rebound is True
        assert await repository.rollback_operation_before_dispatch(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-rebind-rollback",
            owner_epoch=claim.owner_epoch,
            restore_rebound=True,
        )
        restored = await repository.get_operation(operation_id=operation_id)
        assert restored is not None
        assert restored.state == "failed"
        assert restored.event_spool_complete is False
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_terminal_failure_exposes_state_when_spool_overflows(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-terminal-overflow",
            session_key_value="sid-terminal-overflow",
        )
        fingerprint = durable_bridge_hash("terminal-overflow")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-overflow",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-terminal-overflow",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None

        persisted = await repository.append_terminal_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-overflow",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.failed"}\n\n',
            max_bytes=1,
            state="failed",
        )

        assert persisted is False
        failed = await repository.get_operation(operation_id=operation_id)
        assert failed is not None
        assert failed.state == "failed"
        assert failed.event_spool_complete is False
        assert await repository.get_operation_events(operation_id=operation_id) == []
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_terminal_append_failure_settlement_is_visible_to_recovery(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-terminal-recovery",
            session_key_value="sid-terminal-recovery",
        )
        fingerprint = durable_bridge_hash("terminal-recovery")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-terminal-recovery",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.created"}\n\n',
            max_bytes=1024,
        )
        assert await repository.update_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="acknowledged",
            response_id="resp-terminal-recovery",
        )

        replay_fingerprint = durable_bridge_hash("terminal-recovery-replay-alias")
        replay_operation_id = durable_bridge_operation_id(claim.id, replay_fingerprint)
        assert await repository.record_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=replay_fingerprint,
            account_id="account-terminal-recovery",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert await repository.update_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="acknowledged",
            response_id="resp-upstream-replay",
        )
        assert await repository.settle_terminal_append_failure(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="failed",
            expected_response_id="resp-upstream-replay",
            response_id="resp-client-visible-replay",
        )
        replay_operation = await repository.get_operation(operation_id=replay_operation_id)
        assert replay_operation is not None
        assert replay_operation.state == "failed"
        assert replay_operation.response_id == "resp-client-visible-replay"
        assert replay_operation.event_spool_complete is False

        assert await repository.update_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="failed",
            response_id="resp-upstream-replay",
        )
        assert await repository.settle_terminal_append_failure(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="failed",
            expected_response_id="resp-upstream-replay",
            response_id="resp-client-visible-replay",
        )
        pre_settled_replay = await repository.get_operation(operation_id=replay_operation_id)
        assert pre_settled_replay is not None
        assert pre_settled_replay.state == "failed"
        assert pre_settled_replay.response_id == "resp-client-visible-replay"

        assert await repository.update_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="acknowledged",
            response_id="resp-persisted-before-replacement",
        )
        assert await repository.settle_terminal_append_failure(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="failed",
            expected_response_id="resp-unpersisted-replacement",
            alternate_expected_response_id="resp-persisted-before-replacement",
            response_id="resp-client-visible-replay",
        )
        partially_persisted_replay = await repository.get_operation(operation_id=replay_operation_id)
        assert partially_persisted_replay is not None
        assert partially_persisted_replay.state == "failed"
        assert partially_persisted_replay.response_id == "resp-client-visible-replay"

        assert await repository.update_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="acknowledged",
            response_id="resp-upstream-replay",
        )
        assert await repository.settle_terminal_append_failure(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="failed",
            expected_response_id="resp-upstream-replay",
            response_id=None,
        )
        null_alias_settlement = await repository.get_operation(operation_id=replay_operation_id)
        assert null_alias_settlement is not None
        assert null_alias_settlement.state == "failed"
        assert null_alias_settlement.response_id == "resp-upstream-replay"

        assert await repository.update_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="unknown",
        )
        assert await repository.claim_unknown_operation_for_recovery(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
        )
        assert await repository.update_operation(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="acknowledged",
            response_id="resp-upstream-replay",
        )
        assert not await repository.append_terminal_operation_event(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.failed"}\n\n',
            max_bytes=1024,
            state="failed",
            expected_recovery_dispatch_count=0,
            response_id="resp-client-visible-replay",
        )
        assert not await repository.settle_terminal_append_failure(
            operation_id=replay_operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-recovery",
            owner_epoch=claim.owner_epoch,
            state="failed",
            expected_response_id="resp-upstream-replay",
            expected_recovery_dispatch_count=0,
            response_id="resp-client-visible-replay",
        )
        newer_attempt = await repository.get_operation(operation_id=replay_operation_id)
        assert newer_attempt is not None
        assert newer_attempt.state == "acknowledged"
        assert newer_attempt.recovery_dispatch_count == 1
        assert newer_attempt.event_spool_complete is False
    finally:
        await session.close()

    coordinator = DurableBridgeSessionCoordinator(async_session_factory)
    append_terminal_operation_event = coordinator.append_terminal_operation_event
    settle_terminal_append_failure = coordinator.settle_terminal_append_failure
    settlement_finished = asyncio.Event()

    async def fail_terminal_append(**kwargs: Any) -> bool:
        assert await append_terminal_operation_event(**kwargs)
        raise RuntimeError("injected post-commit terminal append failure")

    async def track_terminal_settlement(**kwargs: Any) -> bool:
        try:
            return await settle_terminal_append_failure(**kwargs)
        finally:
            settlement_finished.set()

    monkeypatch.setattr(coordinator, "append_terminal_operation_event", fail_terminal_append)
    monkeypatch.setattr(coordinator, "settle_terminal_append_failure", track_terminal_settlement)
    batcher = HttpBridgeOperationEventBatcher(
        coordinator,
        max_bytes=1024,
        flush_interval_seconds=60.0,
    )

    append_result = await batcher.append_terminal_event(
        operation_id=operation_id,
        session_id=claim.id,
        instance_id="inst-terminal-recovery",
        owner_epoch=claim.owner_epoch,
        event_text='data: {"type":"response.failed"}\n\n',
        max_bytes=1024,
        state="failed",
        response_id="resp-terminal-recovery",
    )
    assert append_result.persisted is False
    assert append_result.settlement_required is True
    await batcher.settle_terminal_event(
        operation_id=operation_id,
        session_id=claim.id,
        instance_id="inst-terminal-recovery",
        owner_epoch=claim.owner_epoch,
        state="failed",
        expected_response_id="resp-terminal-recovery",
        response_id="resp-terminal-recovery",
    )
    await asyncio.wait_for(settlement_finished.wait(), timeout=1.0)

    recovery = DurableBridgeSessionCoordinator(async_session_factory)
    observed = await recovery.get_operation_by_fingerprint(request_fingerprint=fingerprint)
    assert observed is not None
    assert observed.operation_id == operation_id
    assert observed.session_id == claim.id
    assert observed.account_id == "account-terminal-recovery"
    assert observed.state == "failed"
    assert observed.event_spool_complete is False
    assert await recovery.get_operation_events(operation_id=operation_id) == [
        'data: {"type":"response.created"}\n\n',
        'data: {"type":"response.failed"}\n\n',
    ]

    retry = await recovery.record_operation(
        operation_id=operation_id,
        session_id=claim.id,
        instance_id="inst-terminal-recovery",
        owner_epoch=claim.owner_epoch,
        request_fingerprint=fingerprint,
        account_id="account-terminal-recovery",
        model="gpt-5.6",
        parent_response_id="resp-parent",
    )
    assert retry is not None
    assert retry.state == "submitted"
    await batcher.settle_terminal_event(
        operation_id=operation_id,
        session_id=claim.id,
        instance_id="inst-terminal-recovery",
        owner_epoch=claim.owner_epoch,
        state="failed",
        expected_response_id="resp-terminal-recovery",
        response_id="resp-terminal-recovery",
    )
    after_stale_settlement = await recovery.get_operation(operation_id=operation_id)
    assert after_stale_settlement is not None
    assert after_stale_settlement.state == "submitted"
    assert after_stale_settlement.response_id is None
    await batcher.close()


@pytest.mark.asyncio
async def test_consumed_recovery_checkpoint_does_not_rebind_failed_operation(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        original = await _claim(
            repository,
            instance_id="inst-consumed-original",
            session_key_value="sid-consumed-original",
        )
        replacement = await _claim(
            repository,
            instance_id="inst-consumed-replacement",
            session_key_value="sid-consumed-replacement",
        )
        fingerprint = durable_bridge_hash("consumed-failed-operation")
        operation_id = durable_bridge_operation_id(original.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=original.id,
            instance_id="inst-consumed-original",
            owner_epoch=original.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-consumed",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.append_terminal_operation_event(
            operation_id=operation_id,
            session_id=original.id,
            instance_id="inst-consumed-original",
            owner_epoch=original.owner_epoch,
            event_text='data: {"type":"response.failed"}\n\n',
            max_bytes=1024,
            state="failed",
        )

        existing = await repository.record_operation(
            operation_id=operation_id,
            session_id=replacement.id,
            instance_id="inst-consumed-replacement",
            owner_epoch=replacement.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-replacement",
            model="gpt-5.6",
            parent_response_id="resp-parent",
            recovery_attempt_consumed=True,
        )

        assert existing is not None
        assert existing.created is False
        assert existing.session_id == original.id
        assert existing.state == "failed"
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.session_id == original.id
        assert persisted.state == "failed"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_unknown_operation_recovery_claim_is_atomic_and_single_use(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-operation-claim", session_key_value="sid-operation-claim")
        fingerprint = durable_bridge_hash("continuation-claim")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-claim",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.append_operation_event(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-claim",
            owner_epoch=claim.owner_epoch,
            event_text='data: {"type":"response.output_text.delta"}\n\n',
            max_bytes=1024,
        )
        assert await repository.mark_operation_unknown(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-claim",
            owner_epoch=claim.owner_epoch,
        )

        assert await repository.claim_unknown_operation_for_recovery(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-claim",
            owner_epoch=claim.owner_epoch,
        )
        claimed = await repository.get_operation(operation_id=operation_id)
        assert claimed is not None
        assert claimed.state == "submitted"
        assert claimed.response_id is None
        assert claimed.event_spool_complete is False
        assert await repository.get_operation_events(operation_id=operation_id) == []

        # The state transition is the claim: a concurrent reconnect that gets
        # the write lock later cannot reset and submit the same operation.
        assert not await repository.claim_unknown_operation_for_recovery(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-claim",
            owner_epoch=claim.owner_epoch,
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_one_shot_recovery_budget_survives_unknown_reset(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-one-shot",
            session_key_value="sid-operation-one-shot",
        )
        fingerprint = durable_bridge_hash("continuation-one-shot")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-one-shot",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.mark_operation_unknown(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-one-shot",
            owner_epoch=claim.owner_epoch,
        )

        assert await repository.claim_unknown_operation_for_recovery(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-one-shot",
            owner_epoch=claim.owner_epoch,
            max_recovery_dispatches=1,
        )
        # A failed or ambiguous dispatch may return the operation to UNKNOWN,
        # but that must not refund the durable one-shot recovery budget.
        assert await repository.mark_operation_unknown(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-one-shot",
            owner_epoch=claim.owner_epoch,
        )
        assert not await repository.claim_unknown_operation_for_recovery(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-one-shot",
            owner_epoch=claim.owner_epoch,
            max_recovery_dispatches=1,
        )
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.state == "unknown"
        assert persisted.recovery_dispatch_count == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_pre_dispatch_recovery_claim_restores_one_shot_budget(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-refund",
            session_key_value="sid-operation-refund",
        )
        fingerprint = durable_bridge_hash("continuation-refund")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-refund",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None
        assert await repository.mark_operation_unknown(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-refund",
            owner_epoch=claim.owner_epoch,
        )
        assert await repository.claim_unknown_operation_for_recovery(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-refund",
            owner_epoch=claim.owner_epoch,
            max_recovery_dispatches=1,
        )

        # A cancellation before send_text() is proven pre-dispatch and must
        # refund the claim so the next reconnect can make the one safe retry.
        assert await repository.mark_operation_unknown(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-refund",
            owner_epoch=claim.owner_epoch,
            restore_recovery_dispatch_claim=True,
        )
        assert await repository.claim_unknown_operation_for_recovery(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-refund",
            owner_epoch=claim.owner_epoch,
            max_recovery_dispatches=1,
        )
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.recovery_dispatch_count == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_pre_dispatch_operation_rollback_removes_only_empty_new_row(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-rollback",
            session_key_value="sid-operation-rollback",
        )
        fingerprint = durable_bridge_hash("operation-rollback")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        operation = await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-rollback",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert operation is not None and operation.created is True
        assert await repository.rollback_operation_before_dispatch(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-rollback",
            owner_epoch=claim.owner_epoch,
        )
        assert await repository.get_operation(operation_id=operation_id) is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_operation_spool_purge_expires_stale_nonterminal_rows(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-stale-operation", session_key_value="sid-stale-operation")
        fingerprint = durable_bridge_hash("stale-operation")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-stale-operation",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
            request_text='{"input":"stale"}',
        )
        stale_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
        assert await repository.update_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-stale-operation",
            owner_epoch=claim.owner_epoch,
            state="unknown",
        )
        encoded = encode_durable_bridge_transcript_chunk(("stale-event",))
        session.add(
            HttpBridgeOperationEventChunk(
                operation_id=operation_id,
                first_sequence_number=1,
                event_count=encoded.event_count,
                codec=encoded.codec,
                uncompressed_bytes=encoded.uncompressed_bytes,
                payload=encoded.payload,
                payload_sha256=encoded.payload_sha256,
            )
        )
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(
                updated_at=stale_at,
                spool_format=HTTP_BRIDGE_SPOOL_FORMAT_CHUNKS_V2,
            )
        )
        await session.commit()

        # A stale timestamp alone must not delete an UNKNOWN operation whose
        # session is still owned and leased; it may be a long-running recovery
        # request whose duplicate-suppression fence must remain intact.
        assert await repository.purge_operation_spool(cutoff=datetime.now(timezone.utc).replace(tzinfo=None)) == 0
        assert (
            await session.scalar(
                select(HttpBridgeOperationEventChunk).where(HttpBridgeOperationEventChunk.operation_id == operation_id)
            )
            is not None
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(owner_instance_id=None, lease_expires_at=None)
        )
        await session.commit()
        assert await repository.purge_operation_spool(cutoff=datetime.now(timezone.utc).replace(tzinfo=None)) == 1
        assert await repository.get_operation(operation_id=operation_id) is None
        assert (
            await session.scalar(
                select(HttpBridgeOperationEventChunk).where(HttpBridgeOperationEventChunk.operation_id == operation_id)
            )
            is None
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stale_ambiguous_operation_is_abandoned_and_late_writers_are_fenced(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-abandonment",
            session_key_value="sid-operation-abandonment",
        )
        fingerprint = durable_bridge_hash("operation-abandonment")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-abandonment",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        stale_at = utcnow() - timedelta(hours=3)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(state="unknown", updated_at=stale_at)
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(owner_instance_id=None, lease_expires_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()

        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
        )
        assert len(sweep.abandonments) == 1
        assert sweep.abandonments[0].source_state == "unknown"
        assert sweep.abandonments[0].owner_lease_outcome == "ownerless"

        successor = await _claim(
            repository,
            instance_id="inst-operation-abandonment-successor",
            session_key_value="sid-operation-abandonment",
            allow_takeover=True,
        )
        existing = await repository.record_operation(
            operation_id=operation_id,
            session_id=successor.id,
            instance_id="inst-operation-abandonment-successor",
            owner_epoch=successor.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert existing is not None
        assert existing.created is False
        assert existing.state == "abandoned"
        assert (
            await repository.update_operation(
                operation_id=operation_id,
                session_id=successor.id,
                instance_id="inst-operation-abandonment-successor",
                owner_epoch=successor.owner_epoch,
                state="completed",
                response_id="resp-should-not-write",
            )
            is False
        )
        assert (
            await repository.append_operation_event(
                operation_id=operation_id,
                session_id=successor.id,
                instance_id="inst-operation-abandonment-successor",
                owner_epoch=successor.owner_epoch,
                event_text="data: late\n\n",
                max_bytes=1024,
            )
            is False
        )
        assert (
            await repository.append_operation_events(
                events=[
                    DurableBridgeOperationEventInput(
                        operation_id=operation_id,
                        session_id=successor.id,
                        instance_id="inst-operation-abandonment-successor",
                        owner_epoch=successor.owner_epoch,
                        event_text="data: late-batch\n\n",
                    )
                ],
                max_bytes=1024,
            )
            is False
        )
        assert (
            await repository.append_terminal_operation_event(
                operation_id=operation_id,
                session_id=successor.id,
                instance_id="inst-operation-abandonment-successor",
                owner_epoch=successor.owner_epoch,
                event_text="data: late-terminal\n\n",
                max_bytes=1024,
                state="failed",
            )
            is False
        )
        assert (
            await repository.claim_unknown_operation_for_recovery(
                operation_id=operation_id,
                session_id=successor.id,
                instance_id="inst-operation-abandonment-successor",
                owner_epoch=successor.owner_epoch,
            )
            is False
        )
        assert (
            await repository.reset_operation_event_spool(
                operation_id=operation_id,
                session_id=successor.id,
                instance_id="inst-operation-abandonment-successor",
                owner_epoch=successor.owner_epoch,
            )
            is False
        )
        assert (
            await repository.settle_terminal_append_failure(
                operation_id=operation_id,
                session_id=successor.id,
                instance_id="inst-operation-abandonment-successor",
                owner_epoch=successor.owner_epoch,
                state="failed",
                expected_response_id=None,
            )
            is False
        )
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.state == "abandoned"
        assert persisted.response_id is None
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ("completed", "incomplete"))
async def test_sweep_never_abandons_terminal_rows_awaiting_spool_finalization(
    async_session_factory: Callable[[], AsyncSession],
    terminal_state: str,
) -> None:
    """The two-phase terminal write is fenced by state, not by the batcher set.

    ``flush_operation`` drops the operation from the in-memory protection set
    before awaiting ``finalize_operation_event_spool``. That gap is safe
    because the terminal state has already landed by then and the sweep only
    selects ``unknown``/``acknowledged`` rows; even a fully unprotected,
    ownerless, long-inactive terminal row must survive the sweep so the
    pending ``event_spool_complete`` marker still has a row to land on.
    """
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-terminal-finalize",
            session_key_value=f"sid-terminal-finalize-{terminal_state}",
        )
        fingerprint = durable_bridge_hash(f"terminal-finalize-{terminal_state}")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-terminal-finalize",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        # Phase one of the terminal write has landed; phase two (the
        # ``event_spool_complete`` marker) is still in flight. Age the row
        # well past any cutoff and release the owner so nothing except the
        # state predicate stands between it and the sweep.
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(
                state=terminal_state,
                event_spool_complete=False,
                updated_at=utcnow() - timedelta(hours=3),
            )
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(owner_instance_id=None, lease_expires_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()

        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
            protected_operation_ids=(),
        )
        assert sweep.abandonments == ()

        row = await session.scalar(
            select(HttpBridgeOperationRecord).where(HttpBridgeOperationRecord.operation_id == operation_id)
        )
        assert row is not None
        assert row.state == terminal_state
        assert row.event_spool_complete is False
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_abandoned_chunk_operation_fences_late_owner_chunk_writers(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lease-expired owner's late chunks_v2 writes must not resurrect ``abandoned``.

    The sweep leaves session ownership in place, so the original owner still
    matches the instance/epoch fence in ``_lock_operation_for_chunk_append``.
    Without an abandoned-state fence there, a late terminal chunk rewrites
    ``state`` to ``completed`` and a late batch grows the abandoned spool.
    """
    session = async_session_factory()
    try:

        async def encode_off_loop(function, *args):
            return function(*args)

        monkeypatch.setattr(durable_repo_module.asyncio, "to_thread", AsyncMock(side_effect=encode_off_loop))
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-chunk-abandonment",
            session_key_value="sid-chunk-abandonment",
        )
        fingerprint = durable_bridge_hash("chunk-abandonment")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-chunk-abandonment",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
            request_text='{"input":"turn"}',
        )
        assert await repository.append_operation_event_chunk(
            events=[
                DurableBridgeOperationEventInput(
                    operation_id=operation_id,
                    session_id=claim.id,
                    instance_id="inst-chunk-abandonment",
                    owner_epoch=claim.owner_epoch,
                    event_text="created",
                )
            ],
            max_bytes=1024,
        )
        stale_at = utcnow() - timedelta(hours=3)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(state="acknowledged", updated_at=stale_at)
        )
        # The owner keeps its row but its lease lapsed past the grace window.
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(lease_expires_at=utcnow() - timedelta(minutes=10))
        )
        await session.commit()

        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(minutes=2),
        )
        assert [(a.source_state, a.owner_lease_outcome) for a in sweep.abandonments] == [("acknowledged", "expired")]

        assert (
            await repository.append_operation_event_chunk(
                events=[
                    DurableBridgeOperationEventInput(
                        operation_id=operation_id,
                        session_id=claim.id,
                        instance_id="inst-chunk-abandonment",
                        owner_epoch=claim.owner_epoch,
                        event_text="late-delta",
                    )
                ],
                max_bytes=1024,
            )
            is False
        )
        assert (
            await repository.append_terminal_operation_chunk(
                operation_id=operation_id,
                session_id=claim.id,
                instance_id="inst-chunk-abandonment",
                owner_epoch=claim.owner_epoch,
                event_text='data: {"type":"response.completed"}\n\n',
                max_bytes=1024,
                state="completed",
                response_id="resp-should-not-write",
            )
            is False
        )
        # An oversized late terminal must not settle the row either.
        assert (
            await repository.append_terminal_operation_chunk(
                operation_id=operation_id,
                session_id=claim.id,
                instance_id="inst-chunk-abandonment",
                owner_epoch=claim.owner_epoch,
                event_text="x" * 2048,
                max_bytes=1024,
                state="failed",
            )
            is False
        )

        session.expire_all()
        row = await session.get(HttpBridgeOperationRecord, operation_id)
        assert row is not None
        assert row.state == "abandoned"
        assert row.response_id is None
        assert row.event_spool_complete is False
        assert row.event_bytes == len(b"created")
        chunk_count = await session.scalar(
            select(func.count())
            .select_from(HttpBridgeOperationEventChunk)
            .where(HttpBridgeOperationEventChunk.operation_id == operation_id)
        )
        assert chunk_count == 1
        assert await repository.get_replayable_transcript(response_id="resp-should-not-write") is None
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("append_mode", ("single", "batch"))
async def test_durable_event_progress_fences_abandonment_cas(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    append_mode: str,
) -> None:
    session = async_session_factory()
    try:

        @asynccontextmanager
        async def no_writer_lock() -> AsyncIterator[None]:
            yield

        monkeypatch.setattr(durable_bridge_repository_module, "sqlite_writer_section", no_writer_lock)
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-event-race",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-event-race",
        )
        fingerprint = durable_bridge_hash("operation-event-race")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-event-race",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        stale_at = utcnow() - timedelta(hours=3)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(state="acknowledged", updated_at=stale_at)
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(lease_expires_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()

        original_execute = session.execute
        injected = False

        async def append_status_proof_before_cas(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal injected
            if not injected and isinstance(statement, Update) and statement.table.name == "http_bridge_operations":
                injected = True
                if append_mode == "single":
                    persisted_event = await repository.append_operation_event(
                        operation_id=operation_id,
                        session_id=claim.id,
                        instance_id="inst-operation-event-race",
                        owner_epoch=claim.owner_epoch,
                        event_text="data: response.in_progress\n\n",
                        max_bytes=1024,
                    )
                else:
                    persisted_event = await repository.append_operation_events(
                        events=[
                            DurableBridgeOperationEventInput(
                                operation_id=operation_id,
                                session_id=claim.id,
                                instance_id="inst-operation-event-race",
                                owner_epoch=claim.owner_epoch,
                                event_text="data: response.in_progress\n\n",
                            )
                        ],
                        max_bytes=1024,
                    )
                assert persisted_event is True
                # Keep the inactivity clock stale so this test isolates the
                # durable event-progress fence rather than relying on the
                # current ORM writer's on-update timestamp. This models a
                # competing durable writer that commits event progress while
                # retaining the old inactivity clock.
                await original_execute(
                    update(HttpBridgeOperationRecord)
                    .where(HttpBridgeOperationRecord.operation_id == operation_id)
                    .values(updated_at=stale_at)
                )
                await session.commit()
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", append_status_proof_before_cas)
        protected_ids = {f"synthetic-protected-{index}" for index in range(_PROTECTED_OPERATION_ID_SAFE_LIMIT + 1)}
        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
            protected_operation_ids=protected_ids,
        )

        assert injected is True
        assert sweep.abandonments == ()
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.state == "acknowledged"
        assert await repository.get_operation_events(operation_id=operation_id) == ["data: response.in_progress\n\n"]
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("append_mode", ["single", "batch"])
async def test_sweep_abandons_rows_whose_inactivity_clock_was_stamped_by_onupdate(
    async_session_factory: Callable[[], AsyncSession],
    append_mode: str,
) -> None:
    """The CAS must match ``updated_at`` values written by ``onupdate=func.now()``.

    An acknowledged ghost row streamed at least one event before its transport
    was lost, so its last ``updated_at`` write came from the ORM appender's
    ``onupdate`` default: on SQLite that is second-precision text, while the
    loaded datetime binds back with microseconds. An equality predicate on the
    loaded value never matches such rows and the sweep silently no-ops. This
    ages the row with SQLite's own text format instead of a Python datetime.
    """
    session = async_session_factory()
    try:
        bind = session.get_bind()
        if bind is None or bind.dialect.name != "sqlite":
            pytest.skip("SQLite text-form inactivity clock regression")
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-onupdate-clock",
            session_key_value="sid-onupdate-clock",
        )
        fingerprint = durable_bridge_hash("onupdate-clock")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-onupdate-clock",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert await repository.update_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-onupdate-clock",
            owner_epoch=claim.owner_epoch,
            state="acknowledged",
        )
        if append_mode == "single":
            appended = await repository.append_operation_event(
                operation_id=operation_id,
                session_id=claim.id,
                instance_id="inst-onupdate-clock",
                owner_epoch=claim.owner_epoch,
                event_text="data: response.in_progress\n\n",
                max_bytes=1024,
            )
        else:
            appended = await repository.append_operation_events(
                events=[
                    DurableBridgeOperationEventInput(
                        operation_id=operation_id,
                        session_id=claim.id,
                        instance_id="inst-onupdate-clock",
                        owner_epoch=claim.owner_epoch,
                        event_text="data: response.in_progress\n\n",
                    )
                ],
                max_bytes=1024,
            )
        assert appended is True
        # Age the row in the exact text form SQLite's CURRENT_TIMESTAMP
        # produces (no fractional seconds) so the CAS sees the production
        # representation rather than a Python-bound microsecond string.
        await session.execute(
            text(
                "UPDATE http_bridge_operations "
                "SET updated_at = strftime('%Y-%m-%d %H:%M:%S', 'now', '-3 hours') "
                "WHERE operation_id = :operation_id"
            ),
            {"operation_id": operation_id},
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(lease_expires_at=utcnow() - timedelta(minutes=5))
        )
        await session.commit()
        raw_updated_at = await session.scalar(
            text("SELECT updated_at FROM http_bridge_operations WHERE operation_id = :operation_id"),
            {"operation_id": operation_id},
        )
        assert "." not in str(raw_updated_at), raw_updated_at

        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
        )

        assert len(sweep.abandonments) == 1
        assert sweep.abandonments[0].source_state == "acknowledged"
        assert sweep.abandonments[0].owner_lease_outcome == "expired"
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.state == "abandoned"
        assert await repository.get_operation_events(operation_id=operation_id) == ["data: response.in_progress\n\n"]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stale_operation_sweep_protects_live_recently_expired_and_local_pending_id(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        live = await _claim(
            repository,
            instance_id="inst-operation-live",
            lease_ttl_seconds=3600.0,
            session_key_value="sid-operation-live",
        )
        expired = await _claim(
            repository,
            instance_id="inst-operation-expired",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-expired",
        )
        recently_expired = await _claim(
            repository,
            instance_id="inst-operation-recently-expired",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-recently-expired",
        )
        recently_released = await _claim(
            repository,
            instance_id="inst-operation-recently-released",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-recently-released",
        )
        protected = await _claim(
            repository,
            instance_id="inst-operation-protected",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-protected",
        )
        operation_ids: dict[str, str] = {}
        for label, claim, instance_id in (
            ("live", live, "inst-operation-live"),
            ("expired", expired, "inst-operation-expired"),
            ("recently-expired", recently_expired, "inst-operation-recently-expired"),
            ("recently-released", recently_released, "inst-operation-recently-released"),
            ("protected", protected, "inst-operation-protected"),
        ):
            fingerprint = durable_bridge_hash(f"operation-{label}")
            operation_id = durable_bridge_operation_id(claim.id, fingerprint)
            operation_ids[label] = operation_id
            assert await repository.record_operation(
                operation_id=operation_id,
                session_id=claim.id,
                instance_id=instance_id,
                owner_epoch=claim.owner_epoch,
                request_fingerprint=fingerprint,
                account_id="account-operation",
                model="gpt-5.6",
                parent_response_id="resp-parent",
            )

        stale_at = utcnow() - timedelta(hours=3)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id.in_(operation_ids.values()))
            .values(state="acknowledged", updated_at=stale_at)
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id.in_([expired.id, protected.id]))
            .values(lease_expires_at=utcnow() - timedelta(minutes=5))
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == recently_expired.id)
            .values(lease_expires_at=utcnow() - timedelta(seconds=10))
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == recently_released.id)
            .values(owner_instance_id=None, lease_expires_at=utcnow() - timedelta(seconds=10))
        )
        await session.commit()

        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
            protected_operation_ids={operation_ids["protected"]},
        )
        assert [item.source_state for item in sweep.abandonments] == ["acknowledged"]
        expired_operation = await repository.get_operation(operation_id=operation_ids["expired"])
        live_operation = await repository.get_operation(operation_id=operation_ids["live"])
        recently_expired_operation = await repository.get_operation(operation_id=operation_ids["recently-expired"])
        recently_released_operation = await repository.get_operation(operation_id=operation_ids["recently-released"])
        protected_operation = await repository.get_operation(operation_id=operation_ids["protected"])
        assert expired_operation is not None
        assert live_operation is not None
        assert recently_expired_operation is not None
        assert recently_released_operation is not None
        assert protected_operation is not None
        assert expired_operation.state == "abandoned"
        assert live_operation.state == "acknowledged"
        assert recently_expired_operation.state == "acknowledged"
        assert recently_released_operation.state == "acknowledged"
        assert protected_operation.state == "acknowledged"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stale_operation_sweep_bounds_oversized_protection_snapshot(
    async_session_factory: Callable[[], AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-oversized-protection",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-oversized-protection",
        )
        fingerprint = durable_bridge_hash("operation-oversized-protection")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        unprotected_fingerprint = durable_bridge_hash("operation-oversized-unprotected")
        unprotected_operation_id = durable_bridge_operation_id(claim.id, unprotected_fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-operation-oversized-protection",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert await repository.record_operation(
            operation_id=unprotected_operation_id,
            session_id=claim.id,
            instance_id="inst-operation-oversized-protection",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=unprotected_fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        stale_at = utcnow() - timedelta(hours=3)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id.in_((operation_id, unprotected_operation_id)))
            .values(state="acknowledged", updated_at=stale_at)
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(owner_instance_id=None, lease_expires_at=None)
        )
        await session.commit()

        protected_ids = {
            operation_id,
            *(f"synthetic-protected-{index}" for index in range(_PROTECTED_OPERATION_ID_SAFE_LIMIT)),
        }
        assert len(protected_ids) > _PROTECTED_OPERATION_ID_SAFE_LIMIT
        original_execute = session.execute
        locked_candidate_page = False

        async def capture_candidate_lock(statement: Any, *args: Any, **kwargs: Any) -> Any:
            nonlocal locked_candidate_page
            if isinstance(statement, Select) and statement._for_update_arg is not None:
                locked_candidate_page = True
            return await original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(session, "execute", capture_candidate_lock)
        sweep = await repository.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
            protected_operation_ids=protected_ids,
        )
        assert len(sweep.abandonments) == 1
        assert locked_candidate_page is True
        assert sweep.abandonments[0].source_state == "acknowledged"
        protected_operation = await repository.get_operation(operation_id=operation_id)
        unprotected_operation = await repository.get_operation(operation_id=unprotected_operation_id)
        assert protected_operation is not None
        assert unprotected_operation is not None
        assert protected_operation.state == "acknowledged"
        assert unprotected_operation.state == "abandoned"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_stale_operation_sweep_resumes_after_finite_protected_prefix(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-operation-scan-cursor",
            lease_ttl_seconds=1.0,
            session_key_value="sid-operation-scan-cursor",
        )
        stale_at = utcnow() - timedelta(hours=3)
        protected_operation_ids = [f"op-protected-{index:04d}" for index in range(_PROTECTED_OPERATION_SCAN_BUDGET + 1)]
        unprotected_operation_id = "op-unprotected"
        session.add_all(
            [
                HttpBridgeOperationRecord(
                    operation_id=operation_id,
                    session_id=claim.id,
                    request_fingerprint=durable_bridge_hash(operation_id),
                    account_id="account-operation",
                    model="gpt-5.6",
                    state="acknowledged",
                    updated_at=stale_at,
                )
                for operation_id in [*protected_operation_ids, unprotected_operation_id]
            ]
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == claim.id)
            .values(owner_instance_id=None, lease_expires_at=None)
        )
        await session.commit()

        protected_ids = set(protected_operation_ids)
        protected_ids.update(
            f"synthetic-protected-{index}"
            for index in range(_PROTECTED_OPERATION_ID_SAFE_LIMIT - len(protected_ids) + 1)
        )
        assert len(protected_ids) > _PROTECTED_OPERATION_ID_SAFE_LIMIT

        await session.close()
        coordinator = DurableBridgeSessionCoordinator(async_session_factory)
        first_abandonments = await coordinator.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
            protected_operation_ids=protected_ids,
        )
        assert first_abandonments == []
        assert coordinator._operation_abandonment_scan_cursor is not None
        assert (
            coordinator._operation_abandonment_scan_cursor.operation_id
            == protected_operation_ids[_PROTECTED_OPERATION_SCAN_BUDGET - 1]
        )

        second_abandonments = await coordinator.abandon_stale_operations(
            cutoff=utcnow() - timedelta(minutes=30),
            lease_expired_before=utcnow() - timedelta(seconds=30),
            protected_operation_ids=protected_ids,
        )
        assert [item.source_state for item in second_abandonments] == ["acknowledged"]
        assert coordinator._operation_abandonment_scan_cursor is None

        verification_session = async_session_factory()
        verification_repository = DurableBridgeRepository(verification_session)
        unprotected_operation = await verification_repository.get_operation(operation_id=unprotected_operation_id)
        assert unprotected_operation is not None
        assert unprotected_operation.state == "abandoned"
        await verification_session.close()
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_operation_spool_retains_abandoned_row_until_retention_cutoff(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(
            repository,
            instance_id="inst-abandoned-retention",
            session_key_value="sid-abandoned-retention",
        )
        fingerprint = durable_bridge_hash("abandoned-retention")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-abandoned-retention",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id=None,
        )
        stale_at = utcnow() - timedelta(days=8)
        await session.execute(
            update(HttpBridgeOperationRecord)
            .where(HttpBridgeOperationRecord.operation_id == operation_id)
            .values(state="abandoned", updated_at=stale_at)
        )
        await session.commit()

        assert await repository.purge_operation_spool(cutoff=utcnow()) == 1
        assert await repository.get_operation(operation_id=operation_id) is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_nonterminal_operation_rebinds_before_cross_session_recovery_reset(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        original = await _claim(
            repository,
            instance_id="inst-original-operation",
            session_key_value="sid-original-operation",
        )
        replacement = await _claim(
            repository,
            instance_id="inst-replacement-operation",
            session_key_value="sid-replacement-operation",
        )
        fingerprint = durable_bridge_hash("cross-session-operation")
        operation_id = durable_bridge_operation_id(original.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=original.id,
            instance_id="inst-original-operation",
            owner_epoch=original.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
            request_text='{"input":"cross-session"}',
        )
        await session.execute(
            update(HttpBridgeSessionRecord)
            .where(HttpBridgeSessionRecord.id == original.id)
            .values(owner_instance_id=None, lease_expires_at=None)
        )
        await session.commit()
        rebound = await repository.record_operation(
            operation_id=operation_id,
            session_id=replacement.id,
            instance_id="inst-replacement-operation",
            owner_epoch=replacement.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-replacement",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert rebound is not None
        assert rebound.session_id == replacement.id
        assert await repository.reset_operation_event_spool(
            operation_id=operation_id,
            session_id=replacement.id,
            instance_id="inst-replacement-operation",
            owner_epoch=replacement.owner_epoch,
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_nonterminal_operation_does_not_rebind_from_live_prior_owner(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        original = await _claim(
            repository,
            instance_id="inst-live-original-operation",
            session_key_value="sid-live-original-operation",
        )
        replacement = await _claim(
            repository,
            instance_id="inst-live-replacement-operation",
            session_key_value="sid-live-replacement-operation",
        )
        fingerprint = durable_bridge_hash("live-cross-session-operation")
        operation_id = durable_bridge_operation_id(original.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=original.id,
            instance_id="inst-live-original-operation",
            owner_epoch=original.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )

        existing = await repository.record_operation(
            operation_id=operation_id,
            session_id=replacement.id,
            instance_id="inst-live-replacement-operation",
            owner_epoch=replacement.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-replacement",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )

        assert existing is not None
        assert existing.session_id == original.id
        persisted = await repository.get_operation(operation_id=operation_id)
        assert persisted is not None
        assert persisted.session_id == original.id
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_recovery_handoff_rebinds_operation_while_origin_journal_stays_fenced(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        instance_id = "inst-recovery-handoff"
        original = await _claim(
            repository,
            instance_id=instance_id,
            session_key_value="sid-recovery-origin",
        )
        replacement = await _claim(
            repository,
            instance_id=instance_id,
            session_key_value="sid-recovery-replacement",
        )
        operation_fingerprint = durable_bridge_hash("recovery-handoff-operation")
        operation_id = durable_bridge_operation_id(original.id, operation_fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=original.id,
            instance_id=instance_id,
            owner_epoch=original.owner_epoch,
            request_fingerprint=operation_fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        recovery_fingerprint = durable_bridge_hash("recovery-handoff-request")
        attempt = await repository.record_recovery_attempt(
            session_id=original.id,
            instance_id=instance_id,
            owner_epoch=original.owner_epoch,
            request_fingerprint=recovery_fingerprint,
            request_id="request-recovery-handoff",
            account_id="account-operation",
            model="gpt-5.6",
            replay_safe=True,
        )
        assert attempt is not None
        assert await repository.mark_recovery_attempt_replayed(
            session_id=original.id,
            instance_id=instance_id,
            owner_epoch=original.owner_epoch,
            request_fingerprint=recovery_fingerprint,
        )

        rebound = await repository.record_operation(
            operation_id=operation_id,
            session_id=replacement.id,
            instance_id=instance_id,
            owner_epoch=replacement.owner_epoch,
            request_fingerprint=operation_fingerprint,
            account_id="account-replacement",
            model="gpt-5.6",
            parent_response_id="resp-parent",
            recovery_attempt_session_id=original.id,
            recovery_attempt_owner_epoch=original.owner_epoch,
            recovery_attempt_fingerprint=recovery_fingerprint,
        )
        assert rebound is not None
        assert rebound.session_id == replacement.id
        origin = await repository.get_session_by_id(original.id)
        assert origin is not None
        assert origin.owner_instance_id == instance_id
        assert await repository.rollback_recovery_attempt_replayed(
            session_id=original.id,
            instance_id=instance_id,
            owner_epoch=original.owner_epoch,
            request_fingerprint=recovery_fingerprint,
        )
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_startup_retains_completed_operation_session(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-completed-retain", session_key_value="sid-completed-retain")
        fingerprint = durable_bridge_hash("completed-retain")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-completed-retain",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert await repository.update_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-completed-retain",
            owner_epoch=claim.owner_epoch,
            state="completed",
            response_id="resp-completed",
        )
        assert await repository.purge_owned_sessions_on_startup(instance_id="inst-completed-retain") == 0
        retained = await repository.get_operation(operation_id=operation_id)
        assert retained is not None
        owner = await repository.get_session_by_id(claim.id)
        assert owner is not None
        assert owner.owner_instance_id is None
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_startup_retains_completed_operation_session_across_process_epoch(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    session = async_session_factory()
    try:
        repository = DurableBridgeRepository(session)
        claim = await _claim(repository, instance_id="inst-epoch-retain", session_key_value="sid-epoch-retain")
        fingerprint = durable_bridge_hash("epoch-retain")
        operation_id = durable_bridge_operation_id(claim.id, fingerprint)
        assert await repository.record_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-epoch-retain",
            owner_epoch=claim.owner_epoch,
            request_fingerprint=fingerprint,
            account_id="account-operation",
            model="gpt-5.6",
            parent_response_id="resp-parent",
        )
        assert await repository.update_operation(
            operation_id=operation_id,
            session_id=claim.id,
            instance_id="inst-epoch-retain",
            owner_epoch=claim.owner_epoch,
            state="completed",
            response_id="resp-completed",
        )
        assert (
            await repository.purge_owned_sessions_on_startup(
                instance_id="inst-epoch-retain",
                owner_process_epoch="new-process",
            )
            == 0
        )
        owner = await repository.get_session_by_id(claim.id)
        assert owner is not None
        assert owner.owner_instance_id is None
        assert owner.owner_process_epoch == "test-process"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_ring_purge_removes_dead_members_and_keeps_recent(
    async_session_factory: Callable[[], AsyncSession],
) -> None:
    ring = RingMembershipService(async_session_factory)
    await ring.register("instance-dead")
    await ring.register("instance-alive")

    session = async_session_factory()
    try:
        await session.execute(
            update(BridgeRingMember)
            .where(BridgeRingMember.instance_id == "instance-dead")
            .values(last_heartbeat_at=utcnow() - timedelta(hours=25))
        )
        await session.commit()
    finally:
        await session.close()

    purged = await ring.purge_stale_before(utcnow() - timedelta(hours=24))

    assert purged == 1
    session = async_session_factory()
    try:
        result = await session.execute(select(BridgeRingMember.instance_id))
        assert list(result.scalars().all()) == ["instance-alive"]
    finally:
        await session.close()


def _make_app_settings(**overrides: Any) -> Settings:
    return Settings(http_responses_session_bridge_enabled=True, **overrides)


def _make_bridge_session(
    *,
    key_value: str = "bridge-lifecycle",
    account_id: str = "acc-bridge",
) -> proxy_service._HTTPBridgeSession:
    session_key = proxy_service._HTTPBridgeSessionKey("session_header", key_value, None)
    return proxy_service._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-session-id": key_value},
        affinity=proxy_service._AffinityPolicy(
            key=key_value,
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.4",
        account=cast(Any, SimpleNamespace(id=account_id, status=AccountStatus.ACTIVE, plan_type="plus")),
        upstream=cast(Any, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _durable_lookup(
    *,
    session_id: str,
    owner_instance_id: str | None,
    owner_epoch: int,
    state: HttpBridgeSessionState = HttpBridgeSessionState.ACTIVE,
    lease_seconds_from_now: float | None = 60.0,
    latest_turn_state: str | None = None,
) -> proxy_service.DurableBridgeLookup:
    lease_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=lease_seconds_from_now)
        if lease_seconds_from_now is not None
        else None
    )
    return proxy_service.DurableBridgeLookup(
        session_id=session_id,
        canonical_kind="session_header",
        canonical_key="sid-lifecycle",
        api_key_scope="__anonymous__",
        account_id="acc-bridge",
        owner_instance_id=owner_instance_id,
        owner_epoch=owner_epoch,
        lease_expires_at=lease_expires_at,
        state=state,
        latest_turn_state=latest_turn_state,
        latest_response_id=None,
    )


@pytest.mark.asyncio
async def test_fenced_out_renewal_evicts_local_session_and_raises_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    session = _make_bridge_session()
    session.durable_session_id = "durable-fenced"
    session.durable_owner_epoch = 1
    service._http_bridge_sessions[session.key] = session
    service._load_balancer = cast(Any, SimpleNamespace(release_account_lease=AsyncMock()))
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            renew_live_session=AsyncMock(
                return_value=_durable_lookup(
                    session_id="durable-fenced",
                    owner_instance_id="instance-b",
                    owner_epoch=2,
                )
            ),
            release_live_session=AsyncMock(return_value=None),
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._refresh_durable_http_bridge_session(session)

    assert exc_info.value.status_code == 409
    assert exc_info.value.payload["error"]["code"] == "bridge_instance_mismatch"
    assert session.closed is True
    assert session.key not in service._http_bridge_sessions
    # The local epoch must never adopt the foreign owner's epoch.
    assert session.durable_owner_epoch == 1
    await service._drain_http_bridge_background_cleanup_tasks(reason="test")
    cast(Any, session.upstream).close.assert_awaited()
    service._load_balancer.release_account_lease.assert_awaited()


@pytest.mark.asyncio
async def test_owned_renewal_keeps_local_session_open(monkeypatch: pytest.MonkeyPatch) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    session = _make_bridge_session()
    session.durable_session_id = "durable-owned"
    session.durable_owner_epoch = 3
    service._http_bridge_sessions[session.key] = session
    current_instance = proxy_service.get_settings().http_responses_session_bridge_instance_id
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            renew_live_session=AsyncMock(
                return_value=_durable_lookup(
                    session_id="durable-owned",
                    owner_instance_id=current_instance,
                    owner_epoch=3,
                )
            ),
        ),
    )

    await service._refresh_durable_http_bridge_session(session)

    assert session.closed is False
    assert service._http_bridge_sessions[session.key] is session


@pytest.mark.asyncio
async def test_recovery_renewal_outage_evicts_local_session_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    replay_kind, replay_key = make_http_bridge_account_neutral_replay_key("renew-outage")
    session = _make_bridge_session()
    session.key = proxy_service._HTTPBridgeSessionKey(replay_kind, replay_key, None)
    session.durable_session_id = "durable-recovery-renew-outage"
    session.durable_owner_epoch = 3
    service._http_bridge_sessions[session.key] = session
    service._load_balancer = cast(Any, SimpleNamespace(release_account_lease=AsyncMock()))
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            renew_live_session=AsyncMock(side_effect=RuntimeError("database unavailable")),
            release_live_session=AsyncMock(return_value=None),
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._refresh_durable_http_bridge_session(session)

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert session.closed is True
    assert session.key not in service._http_bridge_sessions
    await service._drain_http_bridge_background_cleanup_tasks(reason="test")
    cast(Any, session.upstream).close.assert_awaited()
    service._load_balancer.release_account_lease.assert_awaited()


@pytest.mark.asyncio
async def test_fenced_out_alias_write_evicts_local_session(monkeypatch: pytest.MonkeyPatch) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    session = _make_bridge_session()
    session.durable_session_id = "durable-alias-fenced"
    session.durable_owner_epoch = 2
    service._http_bridge_sessions[session.key] = session
    service._load_balancer = cast(Any, SimpleNamespace(release_account_lease=AsyncMock()))
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            register_turn_state=AsyncMock(return_value=DurableBridgeAliasRegistration.OWNER_FENCED),
            release_live_session=AsyncMock(return_value=None),
        ),
    )

    await service._register_http_bridge_turn_state(session, "turn-fenced")

    assert session.closed is True
    assert session.key not in service._http_bridge_sessions
    await service._drain_http_bridge_background_cleanup_tasks(reason="test")
    cast(Any, session.upstream).close.assert_awaited()
    service._load_balancer.release_account_lease.assert_awaited()


@pytest.mark.asyncio
async def test_alias_fence_rejection_after_same_session_epoch_refresh_does_not_evict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    session = _make_bridge_session()
    session.durable_session_id = "durable-alias-refresh"
    session.durable_owner_epoch = 3
    service._http_bridge_sessions[session.key] = session

    async def reject_turn_state(**_kwargs: Any) -> DurableBridgeAliasRegistration:
        session.durable_owner_epoch = 4
        return DurableBridgeAliasRegistration.OWNER_FENCED

    service._durable_bridge = cast(Any, SimpleNamespace(register_turn_state=reject_turn_state))

    await service._register_http_bridge_turn_state(session, "turn-epoch-refresh")

    assert session.closed is False
    assert service._http_bridge_sessions[session.key] is session
    assert "turn-epoch-refresh" not in session.downstream_turn_state_aliases


@pytest.mark.asyncio
async def test_reconcile_closes_fenced_out_sessions_and_keeps_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    current_instance = proxy_service.get_settings().http_responses_session_bridge_instance_id
    fenced = _make_bridge_session(key_value="sid-fenced")
    fenced.durable_session_id = "durable-sweep-fenced"
    fenced.durable_owner_epoch = 1
    owned = _make_bridge_session(key_value="sid-owned")
    owned.durable_session_id = "durable-sweep-owned"
    owned.durable_owner_epoch = 1
    service._http_bridge_sessions[fenced.key] = fenced
    service._http_bridge_sessions[owned.key] = owned
    service._load_balancer = cast(Any, SimpleNamespace(release_account_lease=AsyncMock()))
    lookup_sessions = AsyncMock(
        return_value=[
            _durable_lookup(
                session_id="durable-sweep-fenced",
                owner_instance_id="instance-b",
                owner_epoch=2,
            ),
            _durable_lookup(
                session_id="durable-sweep-owned",
                owner_instance_id=current_instance,
                owner_epoch=1,
            ),
        ]
    )
    service._durable_bridge = cast(
        Any,
        SimpleNamespace(
            lookup_sessions=lookup_sessions,
            release_live_session=AsyncMock(return_value=None),
        ),
    )

    closed_count = await service.reconcile_durable_http_bridge_ownership()

    assert closed_count == 1
    assert fenced.key not in service._http_bridge_sessions
    assert fenced.closed is True
    assert service._http_bridge_sessions[owned.key] is owned
    assert owned.closed is False
    await service._drain_http_bridge_background_cleanup_tasks(reason="test")
    cast(Any, fenced.upstream).close.assert_awaited()
    cast(Any, owned.upstream).close.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_skips_recently_used_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    busy = _make_bridge_session(key_value="sid-busy")
    busy.durable_session_id = "durable-busy"
    busy.durable_owner_epoch = 1
    busy.last_used_at = time.monotonic()
    service._http_bridge_sessions[busy.key] = busy
    lookup_sessions = AsyncMock(return_value=[])
    service._durable_bridge = cast(Any, SimpleNamespace(lookup_sessions=lookup_sessions))

    closed_count = await service.reconcile_durable_http_bridge_ownership()

    assert closed_count == 0
    lookup_sessions.assert_not_awaited()
    assert service._http_bridge_sessions[busy.key] is busy


def _forward_failure(code: str = "bridge_owner_unreachable") -> ProxyResponseError:
    return ProxyResponseError(
        503,
        proxy_service.openai_error(code, "HTTP bridge owner request failed", error_type="server_error"),
    )


async def _run_turn_state_forward_failure_stream(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fresh_lookup: proxy_service.DurableBridgeLookup | None,
) -> tuple[AsyncMock, AsyncMock, AsyncMock, ProxyResponseError]:
    """Drive _stream_via_http_bridge through an owner-forward failure.

    Returns the create mock, the request-targets lookup mock (initial routing
    lookup plus the post-failure freshness lookup), the alias-only lookup mock
    (must stay unused by the takeover path), and the error raised by the
    stream.
    """

    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    payload = proxy_service.ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "hello"},
    )
    request_state = proxy_service._WebSocketRequestState(
        request_id="req-turn-state-takeover",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        event_queue=asyncio.Queue(),
        transport="http",
    )

    def fake_prepare(
        _payload: proxy_service.ResponsesRequest,
        _headers: dict[str, str] | Any,
        *,
        api_key: proxy_service.ApiKeyData | None,
        api_key_reservation: proxy_service.ApiKeyUsageReservationData | None,
        request_id: str,
        client_ip: str | None = None,
    ) -> tuple[proxy_service._WebSocketRequestState, str]:
        del api_key, api_key_reservation, request_id, client_ip
        return request_state, '{"type":"response.create"}'

    owner_forward = proxy_service._HTTPBridgeOwnerForward(
        owner_instance="instance-b",
        owner_endpoint="http://instance-b",
        key=proxy_service._HTTPBridgeSessionKey("turn_state_header", "http_turn_takeover", None),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings",
        lambda: Settings(
            http_responses_session_bridge_enabled=True,
            http_responses_session_bridge_instance_id="instance-a",
        ),
    )
    monkeypatch.setattr(
        proxy_service,
        "get_settings_cache",
        lambda: cast(
            Any,
            SimpleNamespace(
                get=AsyncMock(
                    return_value=SimpleNamespace(
                        sticky_threads_enabled=False,
                        openai_cache_affinity_max_age_seconds=1800,
                        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
                        http_responses_session_bridge_gateway_safe_mode=False,
                    )
                )
            ),
        ),
    )
    initial_lookup = _durable_lookup(
        session_id="sess-takeover",
        owner_instance_id="instance-b",
        owner_epoch=1,
        latest_turn_state="http_turn_takeover",
    )
    request_targets_mock = AsyncMock(side_effect=[initial_lookup, fresh_lookup])
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", request_targets_mock)
    # The takeover freshness check must reuse the routing lookup semantics
    # (latest-turn-state fallback included); the alias-only lookup would miss
    # rows whose alias registration was lost.
    alias_only_lookup_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(service._durable_bridge, "lookup_turn_state_target", alias_only_lookup_mock)
    monkeypatch.setattr(service, "_prepare_http_bridge_request", fake_prepare)

    local_retry_error = ProxyResponseError(
        500,
        proxy_service.openai_error("local_takeover_attempted", "sentinel", error_type="server_error"),
    )
    create_mock = AsyncMock(side_effect=[owner_forward, local_retry_error])
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", create_mock)

    async def failing_forward(**kwargs: object) -> AsyncIterator[str]:
        del kwargs
        if False:
            yield ""
        raise _forward_failure()

    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", failing_forward)

    with pytest.raises(ProxyResponseError) as exc_info:
        _ = [
            chunk
            async for chunk in service._stream_via_http_bridge(
                payload,
                headers={"x-codex-turn-state": "http_turn_takeover"},
                codex_session_affinity=True,
                propagate_http_errors=False,
                openai_cache_affinity=False,
                api_key=None,
                api_key_reservation=None,
                suppress_text_done_events=False,
                idle_ttl_seconds=120.0,
                codex_idle_ttl_seconds=1800.0,
                max_sessions=8,
                queue_limit=4,
            )
        ]
    return create_mock, request_targets_mock, alias_only_lookup_mock, exc_info.value


@pytest.mark.asyncio
async def test_turn_state_forward_failure_recovers_locally_when_lease_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Post-shutdown grace: released durable lease allows local takeover.

    Before this change the turn-state-anchored request re-raised the
    client-visible 503 even though the owner had already released its lease.
    The freshness lookup must reuse the routing lookup (with its
    latest-turn-state fallback) rather than the alias-only lookup, so rows
    whose alias registration was lost keep their durable anchor.
    """

    released_lookup = _durable_lookup(
        session_id="sess-takeover",
        owner_instance_id=None,
        owner_epoch=2,
        state=HttpBridgeSessionState.DRAINING,
        lease_seconds_from_now=-1.0,
        latest_turn_state="http_turn_takeover",
    )

    create_mock, request_targets_mock, alias_only_lookup_mock, raised = await _run_turn_state_forward_failure_stream(
        monkeypatch,
        fresh_lookup=released_lookup,
    )

    # The sentinel from the local retry proves the takeover path ran instead
    # of surfacing bridge_owner_unreachable.
    assert raised.payload["error"]["code"] == "local_takeover_attempted"
    assert request_targets_mock.await_count == 2
    fresh_lookup_kwargs = request_targets_mock.await_args_list[1].kwargs
    assert fresh_lookup_kwargs["turn_state"] == "http_turn_takeover"
    alias_only_lookup_mock.assert_not_awaited()
    assert create_mock.await_count == 2
    retry_kwargs = create_mock.await_args_list[1].kwargs
    assert retry_kwargs["allow_forward_to_owner"] is False
    assert retry_kwargs["allow_bootstrap_owner_rebind"] is True
    assert retry_kwargs["durable_lookup"] == released_lookup
    assert retry_kwargs["request_stage"] == "reattach"


@pytest.mark.asyncio
async def test_turn_state_forward_failure_fails_closed_with_live_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live_lookup = _durable_lookup(
        session_id="sess-takeover",
        owner_instance_id="instance-b",
        owner_epoch=1,
        state=HttpBridgeSessionState.ACTIVE,
        lease_seconds_from_now=60.0,
        latest_turn_state="http_turn_takeover",
    )

    create_mock, request_targets_mock, _alias_only_lookup_mock, raised = await _run_turn_state_forward_failure_stream(
        monkeypatch,
        fresh_lookup=live_lookup,
    )

    assert raised.status_code == 503
    assert raised.payload["error"]["code"] == "bridge_owner_unreachable"
    assert request_targets_mock.await_count == 2
    assert create_mock.await_count == 1


@pytest.mark.asyncio
async def test_turn_state_forward_failure_fails_closed_when_draining_lease_is_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DRAINING alone must not allow takeover while the owner's lease is live.

    Shutdown marks rows DRAINING before releasing them, so the draining owner
    may still be finishing an in-flight turn; taking over here would create
    concurrent owners for the same bridge session.
    """

    draining_live_lookup = _durable_lookup(
        session_id="sess-takeover",
        owner_instance_id="instance-b",
        owner_epoch=1,
        state=HttpBridgeSessionState.DRAINING,
        lease_seconds_from_now=60.0,
        latest_turn_state="http_turn_takeover",
    )

    create_mock, request_targets_mock, _alias_only_lookup_mock, raised = await _run_turn_state_forward_failure_stream(
        monkeypatch,
        fresh_lookup=draining_live_lookup,
    )

    assert raised.status_code == 503
    assert raised.payload["error"]["code"] == "bridge_owner_unreachable"
    assert request_targets_mock.await_count == 2
    assert create_mock.await_count == 1


def test_durable_lookup_allows_turn_state_takeover_requires_inactive_lease() -> None:
    live_draining = _durable_lookup(
        session_id="sess-1",
        owner_instance_id="instance-b",
        owner_epoch=1,
        state=HttpBridgeSessionState.DRAINING,
        lease_seconds_from_now=60.0,
    )
    expired_draining = _durable_lookup(
        session_id="sess-2",
        owner_instance_id="instance-b",
        owner_epoch=1,
        state=HttpBridgeSessionState.DRAINING,
        lease_seconds_from_now=-1.0,
    )
    released_draining = _durable_lookup(
        session_id="sess-3",
        owner_instance_id=None,
        owner_epoch=1,
        state=HttpBridgeSessionState.DRAINING,
        lease_seconds_from_now=None,
    )
    closed = _durable_lookup(
        session_id="sess-4",
        owner_instance_id="instance-b",
        owner_epoch=1,
        state=HttpBridgeSessionState.CLOSED,
        lease_seconds_from_now=60.0,
    )

    allows = _http_bridge_durable_lookup_allows_turn_state_takeover
    assert allows(None) is True
    assert allows(live_draining) is False
    assert allows(expired_draining) is True
    assert allows(released_draining) is True
    assert allows(closed) is True
    assert _http_bridge_allow_durable_takeover(live_draining) is False
    assert _http_bridge_allow_durable_takeover(expired_draining) is True
    assert _http_bridge_allow_durable_takeover(released_draining) is True
    assert _http_bridge_allow_durable_takeover(closed) is True
    assert _http_bridge_claim_allows_takeover(live_draining, force=True) is False
    assert _http_bridge_claim_allows_takeover(expired_draining, force=True) is True
    assert _http_bridge_claim_allows_takeover(released_draining, force=True) is True
    assert _http_bridge_claim_allows_takeover(closed, force=True) is True
    live_active = _durable_lookup(
        session_id="sess-5",
        owner_instance_id="instance-b",
        owner_epoch=1,
        state=HttpBridgeSessionState.ACTIVE,
        lease_seconds_from_now=60.0,
    )
    assert _http_bridge_claim_allows_takeover(live_active, force=True) is True
    assert _http_bridge_claim_allows_takeover(live_active, force=False) is False


@pytest.mark.asyncio
async def test_claim_does_not_retry_takeover_against_a_live_foreign_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The repository drops takeover permission after losing a claim race, but
    the service's own retry loop issues a fresh claim — which would restore the
    permission and steal the winner's live lease. A live foreign owner must end
    the retry so the 409 'retry to reach the correct replica' response stands
    (issue #1695)."""
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(proxy_service, "get_settings", _make_app_settings)
    session = _make_bridge_session(key_value="sid-foreign-live")
    session.account = cast(Any, SimpleNamespace(id="acc-1", status=AccountStatus.ACTIVE, plan_type="plus"))

    claims: list[bool] = []

    async def claim_live_session(*, allow_takeover, **kwargs):
        claims.append(allow_takeover)
        # A live foreign owner: lease well in the future, ACTIVE.
        return SimpleNamespace(
            session_id="durable-foreign",
            owner_instance_id="instance-other",
            owner_epoch=7,
            lease_expires_at=utcnow() + timedelta(seconds=600),
            state=HttpBridgeSessionState.ACTIVE,
            latest_turn_state=None,
            latest_response_id=None,
            canonical_kind=session.key.affinity_kind,
            canonical_key=session.key.affinity_key,
            account_id="acc-1",
            api_key_scope="__anonymous__",
            lease_is_active=lambda now: True,
        )

    service._durable_bridge = cast(Any, SimpleNamespace(claim_live_session=claim_live_session))

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._claim_durable_http_bridge_session(session, allow_takeover=True)

    assert exc_info.value.status_code == 409
    # Exactly one claim: the live foreign owner ends the retry instead of
    # issuing a second, permission-restoring claim.
    assert claims == [True]
