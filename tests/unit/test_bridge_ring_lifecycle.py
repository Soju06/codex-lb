from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import Settings
from app.core.utils.time import utcnow
from app.db.models import (
    AccountStatus,
    Base,
    BridgeRingMember,
    HttpBridgeSessionAlias,
    HttpBridgeSessionRecord,
    HttpBridgeSessionState,
)
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_durable_lookup_allows_turn_state_takeover,
)
from app.modules.proxy.continuity import make_http_bridge_account_neutral_replay_key
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeAliasRegistration,
    DurableBridgeRepository,
)
from app.modules.proxy.ring_membership import RingMembershipService

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _share_proxy_dashboard_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _SettingsCache:
        async def get(self) -> object:
            return proxy_service.get_settings()

    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache())


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
