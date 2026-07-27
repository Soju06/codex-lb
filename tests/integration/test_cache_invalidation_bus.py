"""Two-replica cache-invalidation bus tests.

Replica A is either the real app (async_client) or a standalone poller; replica B is a
second set of cache/poller instances sharing the same database, following the
tests/integration/test_multi_replica.py pattern. Pollers are driven via _poll_once()
directly for determinism (no sleeps).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import delete as sa_delete
from sqlalchemy import event, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache.invalidation import (
    _NAMESPACE_LOG_LABELS,
    NAMESPACE_ACCOUNT_ROUTING,
    NAMESPACE_ACCOUNT_SELECTION,
    NAMESPACE_API_KEY,
    NAMESPACE_FIREWALL,
    NAMESPACE_MODEL_REGISTRY,
    NAMESPACE_RESET_CREDITS,
    NAMESPACE_SETTINGS,
    NAMESPACE_UPSTREAM_ROUTE,
    CacheInvalidationPoller,
    get_cache_invalidation_poller,
    set_cache_invalidation_poller,
)
from app.core.config.settings_cache import SettingsCache
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    cache_invalidation_bump_failures_total,
    cache_invalidation_poll_failures_total,
)
from app.db.models import Account, AccountStatus, CacheInvalidation
from app.db.session import SessionLocal, engine
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_session_account_active
from app.modules.proxy.account_cache import (
    AccountSelectionCache,
    RoutingAvailabilityCache,
    get_routing_availability_cache,
    is_account_routing_unavailable,
    mark_account_routing_unavailable,
)

if TYPE_CHECKING:
    from app.modules.proxy._service.http_bridge.helpers import _HTTPBridgeSession
    from app.modules.proxy.load_balancer import SelectionInputs

pytestmark = pytest.mark.integration

_INVALIDATION_LOGGER = "app.core.cache.invalidation"


@pytest.fixture
def poller_slot():
    """Save/restore the process-global poller around a test that replaces it."""
    previous = get_cache_invalidation_poller()
    yield
    set_cache_invalidation_poller(previous)


def _make_account(account_id: str, status: AccountStatus = AccountStatus.ACTIVE) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
        status=status,
        deactivation_reason=None,
    )


async def _insert_account(account_id: str, status: AccountStatus = AccountStatus.ACTIVE) -> None:
    async with SessionLocal() as session:
        session.add(_make_account(account_id, status))
        await session.commit()


async def _set_account_status(account_id: str, status: AccountStatus) -> None:
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == account_id).values(status=status))
        await session.commit()


async def _delete_account_row(account_id: str) -> None:
    async with SessionLocal() as session:
        await session.execute(sa_delete(Account).where(Account.id == account_id))
        await session.commit()


async def _namespace_version(namespace: str) -> int | None:
    async with SessionLocal() as session:
        return await session.scalar(select(CacheInvalidation.version).where(CacheInvalidation.namespace == namespace))


def _fake_bridge_session(account: Account) -> "_HTTPBridgeSession":
    return cast("_HTTPBridgeSession", SimpleNamespace(account=account))


def _make_replica_b_routing() -> tuple[RoutingAvailabilityCache, CacheInvalidationPoller]:
    cache = RoutingAvailabilityCache(SessionLocal)
    poller = CacheInvalidationPoller(SessionLocal)
    poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, cache.refresh_from_db)
    return cache, poller


@pytest.mark.asyncio
async def test_pause_via_api_marks_peer_routing_unavailable(async_client, db_setup) -> None:
    """Pausing an account through the API on replica A converges on replica B."""
    account_id = "acct-bus-pause"
    await _insert_account(account_id)

    b_cache, b_poller = _make_replica_b_routing()
    await b_cache.refresh_from_db()
    await b_poller._poll_once()
    assert b_cache.is_unavailable(account_id) is False

    response = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert response.status_code == 200

    # The pause endpoint awaits a durable account_routing bump before returning,
    # so a single peer poll converges.
    await b_poller._poll_once()
    assert b_cache.is_unavailable(account_id) is True


@pytest.mark.asyncio
async def test_remote_pause_stops_stale_bridge_session_reuse(db_setup, poller_slot) -> None:
    """A warm bridge session pinned to a stale ACTIVE account snapshot is refused
    once a peer's pause converges over the bus (product path: helpers.py reuse gate)."""
    account_id = "acct-bus-bridge-pause"
    await _insert_account(account_id)

    local_poller = CacheInvalidationPoller(SessionLocal)
    routing_cache = get_routing_availability_cache()
    local_poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, routing_cache.refresh_from_db)
    set_cache_invalidation_poller(local_poller)
    await routing_cache.refresh_from_db()
    await local_poller._poll_once()

    stale_session = _fake_bridge_session(_make_account(account_id, AccountStatus.ACTIVE))
    assert _http_bridge_session_account_active(stale_session) is True

    # Replica A pauses the account: committed status write + durable bump.
    await _set_account_status(account_id, AccountStatus.PAUSED)
    remote_poller = CacheInvalidationPoller(SessionLocal)
    assert await remote_poller.bump(NAMESPACE_ACCOUNT_ROUTING) is True

    await local_poller._poll_once()
    assert _http_bridge_session_account_active(stale_session) is False


@pytest.mark.asyncio
async def test_reauth_on_peer_clears_local_routing_marker(db_setup, poller_slot) -> None:
    """A routing-unavailable marker set locally is cleared when another replica
    re-authenticates the account (previously permanent until restart)."""
    account_id = "acct-bus-reauth"
    await _insert_account(account_id)

    local_poller = CacheInvalidationPoller(SessionLocal)
    routing_cache = get_routing_availability_cache()
    local_poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, routing_cache.refresh_from_db)
    set_cache_invalidation_poller(local_poller)
    await routing_cache.refresh_from_db()
    await local_poller._poll_once()

    # Local permanent refresh failure: committed status write + local marker.
    await _set_account_status(account_id, AccountStatus.REAUTH_REQUIRED)
    mark_account_routing_unavailable(account_id)
    assert is_account_routing_unavailable(account_id) is True

    # Replica A re-authenticates the account: committed status write + durable bump.
    await _set_account_status(account_id, AccountStatus.ACTIVE)
    remote_poller = CacheInvalidationPoller(SessionLocal)
    assert await remote_poller.bump(NAMESPACE_ACCOUNT_ROUTING) is True

    await local_poller._poll_once()
    assert is_account_routing_unavailable(account_id) is False


@pytest.mark.asyncio
async def test_deleted_account_unroutable_on_peer_despite_stale_active_snapshot(db_setup, poller_slot) -> None:
    account_id = "acct-bus-delete"
    await _insert_account(account_id)

    local_poller = CacheInvalidationPoller(SessionLocal)
    routing_cache = get_routing_availability_cache()
    local_poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, routing_cache.refresh_from_db)
    set_cache_invalidation_poller(local_poller)
    await routing_cache.refresh_from_db()
    await local_poller._poll_once()

    stale_session = _fake_bridge_session(_make_account(account_id, AccountStatus.ACTIVE))
    assert _http_bridge_session_account_active(stale_session) is True

    await _delete_account_row(account_id)
    remote_poller = CacheInvalidationPoller(SessionLocal)
    assert await remote_poller.bump(NAMESPACE_ACCOUNT_ROUTING) is True

    await local_poller._poll_once()
    assert _http_bridge_session_account_active(stale_session) is False


@pytest.mark.asyncio
async def test_selection_cache_invalidation_propagates_to_peer(db_setup, poller_slot) -> None:
    """Replica A's selection-cache invalidation clears replica B's cache via the bus
    instead of waiting out the 5s TTL, and converged pollers do not re-bump."""
    a_cache = AccountSelectionCache(ttl_seconds=5)
    b_cache = AccountSelectionCache(ttl_seconds=5)

    poller_a = CacheInvalidationPoller(SessionLocal)
    set_cache_invalidation_poller(poller_a)
    poller_b = CacheInvalidationPoller(SessionLocal)
    poller_b.on_invalidation(NAMESPACE_ACCOUNT_SELECTION, lambda: b_cache.invalidate(propagate=False))

    await poller_a._poll_once()
    await poller_b._poll_once()

    sentinel = cast("SelectionInputs", object())
    await b_cache.set(sentinel)
    assert await b_cache.get() is sentinel

    # Replica A invalidates with propagation (the default for all call sites).
    a_cache.invalidate()

    # Before replica B polls, it still serves the stale entry (the defect window).
    assert await b_cache.get() is sentinel

    await poller_a._poll_once()  # flushes the coalesced bump
    await poller_b._poll_once()  # runs replica B's callback
    assert await b_cache.get() is None

    # No feedback loop: converged pollers must not keep bumping the version.
    version = await _namespace_version(NAMESPACE_ACCOUNT_SELECTION)
    assert version is not None
    for _ in range(3):
        await poller_a._poll_once()
        await poller_b._poll_once()
    assert await _namespace_version(NAMESPACE_ACCOUNT_SELECTION) == version


@pytest.mark.asyncio
async def test_password_setup_propagates_settings_to_peer(async_client, db_setup) -> None:
    """Setting the dashboard password on replica A is visible to replica B's settings
    cache after one poll cycle, without waiting for the 5s TTL."""
    b_settings = SettingsCache()
    poller_b = CacheInvalidationPoller(SessionLocal)
    poller_b.on_invalidation(NAMESPACE_SETTINGS, lambda: b_settings.invalidate(propagate=False))
    await poller_b._poll_once()

    assert (await b_settings.get()).password_hash is None

    response = await async_client.post("/api/dashboard-auth/password/setup", json={"password": "password123"})
    assert response.status_code == 200

    # Replica B still serves the stale row until its next poll (the defect window).
    assert (await b_settings.get()).password_hash is None

    await poller_b._poll_once()
    assert (await b_settings.get()).password_hash is not None


class _FlakySessionFactory:
    """Session factory that raises a lock error for the first N calls."""

    def __init__(self, failures: int) -> None:
        self.remaining = failures

    def __call__(self) -> AsyncSession:
        if self.remaining > 0:
            self.remaining -= 1
            raise OperationalError("stmt", {}, Exception("database is locked"))
        return SessionLocal()


def _counter_value(counter, *label_values: str) -> float:
    metric = counter.labels(*label_values) if label_values else counter
    return metric._value.get()


@pytest.mark.asyncio
async def test_bump_failure_is_observable_and_does_not_raise(db_setup, caplog) -> None:
    namespace = "test_bump_failure"
    poller = CacheInvalidationPoller(_FlakySessionFactory(failures=100))

    before = (
        _counter_value(cache_invalidation_bump_failures_total, namespace)
        if PROMETHEUS_AVAILABLE and cache_invalidation_bump_failures_total is not None
        else None
    )
    with caplog.at_level(logging.ERROR, logger=_INVALIDATION_LOGGER):
        assert await poller.bump(namespace) is False

    # Unregistered namespaces are logged with a log-safe fallback label.
    assert any(
        record.levelno == logging.ERROR
        and "cache_invalidation bump failed for namespace unknown" in record.getMessage()
        for record in caplog.records
    )
    if before is not None:
        assert _counter_value(cache_invalidation_bump_failures_total, namespace) == before + 1
    assert await _namespace_version(namespace) is None


def test_namespace_log_labels_cover_all_namespaces() -> None:
    """_NAMESPACE_LOG_LABELS uses literal keys/values (analyzer-safe); keep in sync."""
    assert _NAMESPACE_LOG_LABELS == {
        namespace: namespace
        for namespace in (
            NAMESPACE_API_KEY,
            NAMESPACE_FIREWALL,
            NAMESPACE_ACCOUNT_ROUTING,
            NAMESPACE_ACCOUNT_SELECTION,
            NAMESPACE_SETTINGS,
            NAMESPACE_RESET_CREDITS,
            NAMESPACE_MODEL_REGISTRY,
            NAMESPACE_UPSTREAM_ROUTE,
        )
    }


@pytest.mark.asyncio
async def test_pending_coalesced_bump_flushes_after_recovery(db_setup) -> None:
    namespace = "test_pending_flush"
    # First cycle: bump retries (3 attempts) and the poll read both fail.
    factory = _FlakySessionFactory(failures=100)
    poller = CacheInvalidationPoller(factory)
    poller.request_bump(namespace)

    await poller._poll_once()
    assert namespace in poller._pending_bumps
    assert await _namespace_version(namespace) is None

    # Database becomes writable again: the next cycle flushes the pending namespace.
    factory.remaining = 0
    await poller._poll_once()
    assert namespace not in poller._pending_bumps
    assert await _namespace_version(namespace) == 1


@pytest.mark.asyncio
async def test_bump_requested_during_flush_survives(db_setup, monkeypatch) -> None:
    """A request_bump() landing while _flush_pending_bumps is awaiting the bump
    write must re-queue the namespace and produce a later bump; a mutation
    committing mid-flush must not be coalesced into the version already written."""
    namespace = "test_flush_race"
    poller = CacheInvalidationPoller(SessionLocal)
    real_bump = poller.bump

    async def bump_then_concurrent_request(ns: str) -> bool:
        ok = await real_bump(ns)
        # Another mutation commits and requests a bump while the flush loop is
        # still awaiting this bump.
        poller.request_bump(ns)
        return ok

    monkeypatch.setattr(poller, "bump", bump_then_concurrent_request)
    poller.request_bump(namespace)
    await poller._flush_pending_bumps()
    assert namespace in poller._pending_bumps  # re-queued, not lost
    assert await _namespace_version(namespace) == 1

    monkeypatch.setattr(poller, "bump", real_bump)
    await poller._flush_pending_bumps()
    assert namespace not in poller._pending_bumps
    assert await _namespace_version(namespace) == 2


@pytest.mark.asyncio
async def test_transient_refresh_failure_does_not_ack_routing_bump(db_setup, caplog) -> None:
    """A transient DB error during the account_routing refresh callback must not
    acknowledge the bump; the next poll cycle retries and converges the pause."""
    account_id = "acct-bus-refresh-retry"
    await _insert_account(account_id)

    flaky = _FlakySessionFactory(failures=0)
    b_cache = RoutingAvailabilityCache(flaky)
    b_poller = CacheInvalidationPoller(SessionLocal)
    b_poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, b_cache.refresh_from_db)
    await b_cache.refresh_from_db()
    await b_poller._poll_once()
    assert b_cache.is_unavailable(account_id) is False

    # Replica A pauses the account: committed status write + durable bump.
    await _set_account_status(account_id, AccountStatus.PAUSED)
    remote_poller = CacheInvalidationPoller(SessionLocal)
    assert await remote_poller.bump(NAMESPACE_ACCOUNT_ROUTING) is True

    # Replica B's refresh hits a transient DB error: the version must stay
    # unacknowledged (the stale ACTIVE snapshot is the defect window).
    flaky.remaining = 1
    with caplog.at_level(logging.WARNING, logger=_INVALIDATION_LOGGER):
        await b_poller._poll_once()
    assert b_cache.is_unavailable(account_id) is False
    assert any(
        "cache_invalidation callback failed for namespace account_routing" in record.getMessage()
        for record in caplog.records
    )

    # The next cycle retries the refresh and converges without another bump.
    await b_poller._poll_once()
    assert b_cache.is_unavailable(account_id) is True


class _MarkMidRefreshSessionFactory:
    """Session factory whose sessions add a local routing mark right after the
    status SELECT returns, while refresh_from_db is still in flight.

    This reproduces the lost-update race: the SELECT read a pre-pause ACTIVE
    row, then the pause commits and mark_unavailable() runs before the refresh
    finishes rebuilding the snapshot.
    """

    def __init__(self) -> None:
        self.cache: RoutingAvailabilityCache | None = None
        self.account_id = ""
        self.armed = False

    def __call__(self) -> AsyncSession:
        return cast(AsyncSession, _MarkMidRefreshSession(SessionLocal(), self))


class _MarkMidRefreshSession:
    def __init__(self, inner: AsyncSession, factory: _MarkMidRefreshSessionFactory) -> None:
        self._inner = inner
        self._factory = factory

    async def execute(self, *args, **kwargs):
        result = await self._inner.execute(*args, **kwargs)
        if self._factory.armed:
            self._factory.armed = False
            assert self._factory.cache is not None
            self._factory.cache.mark_unavailable(self._factory.account_id)
        return result

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_local_mark_during_inflight_refresh_survives(db_setup, poller_slot, monkeypatch) -> None:
    """A local routing-unavailable mark added while a snapshot refresh is in
    flight must not be dropped by that refresh's stale ACTIVE snapshot; the
    bridge reuse gate must refuse the account immediately after the refresh."""
    account_id = "acct-bus-mark-race"
    await _insert_account(account_id)

    factory = _MarkMidRefreshSessionFactory()
    cache = RoutingAvailabilityCache(factory)
    factory.cache = cache
    factory.account_id = account_id
    monkeypatch.setattr("app.modules.proxy.account_cache._routing_availability_cache", cache)
    set_cache_invalidation_poller(CacheInvalidationPoller(SessionLocal))

    await cache.refresh_from_db()  # seed (unarmed)
    assert cache.is_unavailable(account_id) is False

    # A refresh whose SELECT read the pre-pause ACTIVE row is still in flight
    # when the permanent-failure mark lands; the stale snapshot must not
    # filter the fresh mark away.
    factory.armed = True
    await cache.refresh_from_db()
    assert cache.is_unavailable(account_id) is True

    stale_session = _fake_bridge_session(_make_account(account_id, AccountStatus.ACTIVE))
    assert _http_bridge_session_account_active(stale_session) is False

    # Once the paused status is committed, later refreshes keep the account
    # unavailable via the snapshot itself.
    await _set_account_status(account_id, AccountStatus.PAUSED)
    await cache.refresh_from_db()
    assert cache.is_unavailable(account_id) is True

    # Pre-existing marks are still cleared by a refresh that observes a
    # committed routable status (reactivation on a peer).
    await _set_account_status(account_id, AccountStatus.ACTIVE)
    await cache.refresh_from_db()
    assert cache.is_unavailable(account_id) is False


class _BrokenSession:
    def in_transaction(self) -> bool:
        return False

    async def execute(self, *args, **kwargs):
        raise OperationalError("stmt", {}, Exception("poll read failed"))

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_consecutive_poll_failures_escalate_to_warning(db_setup, caplog) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _BrokenSession()))

    before = (
        _counter_value(cache_invalidation_poll_failures_total)
        if PROMETHEUS_AVAILABLE and cache_invalidation_poll_failures_total is not None
        else None
    )
    with caplog.at_level(logging.DEBUG, logger=_INVALIDATION_LOGGER):
        await poller._poll_once()
        await poller._poll_once()
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)
        await poller._poll_once()

    assert any(
        record.levelno == logging.WARNING and "3 consecutive" in record.getMessage() for record in caplog.records
    )
    if before is not None:
        assert _counter_value(cache_invalidation_poll_failures_total) == before + 3


@pytest.mark.asyncio
async def test_bridge_reuse_check_is_pure_in_memory(db_setup, poller_slot) -> None:
    """The bridge-session reuse gate must not issue database queries per request."""
    account_id = "acct-bus-hotpath"
    await _insert_account(account_id)

    local_poller = CacheInvalidationPoller(SessionLocal)
    routing_cache = get_routing_availability_cache()
    local_poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, routing_cache.refresh_from_db)
    set_cache_invalidation_poller(local_poller)
    await routing_cache.refresh_from_db()
    await local_poller._poll_once()

    stale_session = _fake_bridge_session(_make_account(account_id, AccountStatus.ACTIVE))

    statements: list[str] = []

    def _record_statement(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record_statement)
    try:
        for _ in range(100):
            assert _http_bridge_session_account_active(stale_session) is True
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _record_statement)

    assert statements == []


@pytest.mark.asyncio
async def test_initialize_seeds_baseline_so_bump_before_first_poll_fires(db_setup) -> None:
    """A peer bump committed after baseline seeding but before the first poll must be
    observed as a change, not acknowledged as pre-existing state.

    Seeding the baseline at startup (before serving traffic) closes the window where
    a peer bump landing before the first poll would otherwise be treated as a baseline
    and silently dropped until the fallback TTL.
    """
    # A peer has already bumped the namespace at least once before this replica
    # starts, so a version row exists at some baseline version V.
    peer = CacheInvalidationPoller(SessionLocal)
    assert await peer.bump(NAMESPACE_ACCOUNT_ROUTING) is True
    baseline = await _namespace_version(NAMESPACE_ACCOUNT_ROUTING)
    assert baseline is not None

    calls: list[str] = []
    poller = CacheInvalidationPoller(SessionLocal)
    poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, lambda: calls.append("routing"))

    # Seed baseline before loading local caches / serving traffic.
    await poller.initialize()
    assert poller._known_versions.get(NAMESPACE_ACCOUNT_ROUTING) == baseline

    # A peer commits a mutation and bumps AFTER the baseline seed but before this
    # replica's first poll cycle.
    assert await peer.bump(NAMESPACE_ACCOUNT_ROUTING) is True

    # The first poll observes the bump as a change and runs the callback.
    await poller._poll_once()
    assert calls == ["routing"]

    # Contrast: a poller that skips the baseline seed treats the already-bumped row
    # as a baseline on its first poll and never fires the callback (the defect).
    naive_calls: list[str] = []
    naive = CacheInvalidationPoller(SessionLocal)
    naive.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, lambda: naive_calls.append("routing"))
    await naive._poll_once()
    assert naive_calls == []


@pytest.mark.asyncio
async def test_initialize_failure_leaves_poller_uninitialized(db_setup) -> None:
    """A failed baseline seed raises with state unchanged so the caller can degrade
    to first-poll-baselines instead of half-seeding."""
    poller = CacheInvalidationPoller(_FlakySessionFactory(failures=100))
    with pytest.raises(OperationalError):
        await poller.initialize()
    assert poller._poll_initialized is False
    assert poller._known_versions == {}


@pytest.mark.asyncio
async def test_bump_local_suppresses_source_callback_but_peer_still_fires(db_setup) -> None:
    """A replica that has already invalidated locally uses ``bump_local`` so its
    OWN poller does not re-fire the (whole-store) callback for the bump it just
    issued, while a peer replica still observes it and fires. This is the
    reset-credit self-clear regression: without ``bump_local`` the source poller
    would clear its entire reset-credit store on its own bump."""
    source_calls: list[str] = []
    peer_calls: list[str] = []

    source = CacheInvalidationPoller(SessionLocal)
    source.on_invalidation(NAMESPACE_RESET_CREDITS, lambda: source_calls.append("cleared"))
    peer = CacheInvalidationPoller(SessionLocal)
    peer.on_invalidation(NAMESPACE_RESET_CREDITS, lambda: peer_calls.append("cleared"))

    # Both replicas seed their baselines before anything is bumped.
    await source._poll_once()
    await peer._poll_once()

    # The source has already invalidated the affected account locally; it only
    # needs peers to react.
    assert await source.bump_local(NAMESPACE_RESET_CREDITS) is True

    # The source poll does NOT re-run its whole-store callback for its own bump.
    await source._poll_once()
    assert source_calls == []

    # The peer observes the bump and clears its store exactly once.
    await peer._poll_once()
    assert peer_calls == ["cleared"]

    # A genuine peer bump still fires on the source (self-suppression only
    # cancels the source's own contribution, never a peer's).
    assert await peer.bump(NAMESPACE_RESET_CREDITS) is True
    await source._poll_once()
    assert source_calls == ["cleared"]


class _BumpLocalMidPollSession:
    """Session wrapper that runs a one-shot ``bump_local`` on the owning poller
    right after that poller's snapshot SELECT returns.

    This reproduces the interleaving where ``bump_local()`` advances
    ``_known_versions`` (recording a local self-suppression) after ``_poll_once``
    has already read the pre-bump snapshot but before it processes that stale row.
    """

    def __init__(self, inner: AsyncSession, hook: _BumpLocalMidPollHook) -> None:
        self._inner = inner
        self._hook = hook

    async def execute(self, *args, **kwargs):
        result = await self._inner.execute(*args, **kwargs)
        if self._hook.armed:
            self._hook.armed = False
            assert self._hook.poller is not None
            await self._hook.poller.bump_local(NAMESPACE_RESET_CREDITS)
        return result

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _BumpLocalMidPollHook:
    def __init__(self) -> None:
        self.poller: CacheInvalidationPoller | None = None
        self.armed = False

    def __call__(self) -> AsyncSession:
        return cast(AsyncSession, _BumpLocalMidPollSession(SessionLocal(), self))


@pytest.mark.asyncio
async def test_inflight_poll_does_not_clobber_concurrent_local_bump(db_setup) -> None:
    """A ``bump_local`` that advances ``_known_versions`` while a poll cycle is in
    flight (snapshot already read) must not be clobbered: the older in-flight poll
    observation must neither re-fire the whole-store callback nor rewind the
    acknowledged version back below the local ack."""
    source_calls: list[str] = []
    hook = _BumpLocalMidPollHook()
    source = CacheInvalidationPoller(hook)
    hook.poller = source
    source.on_invalidation(NAMESPACE_RESET_CREDITS, lambda: source_calls.append("cleared"))

    # Seed a baseline and acknowledge version 1 without the race in play.
    await source._poll_once()
    assert await source.bump(NAMESPACE_RESET_CREDITS) is True
    await source._poll_once()
    assert source._known_versions.get(NAMESPACE_RESET_CREDITS) == 1
    source_calls.clear()

    # Arm the race: the next poll's snapshot SELECT reads version 1, then a
    # concurrent bump_local advances the DB and _known_versions to 2 before the
    # poll processes its stale row.
    hook.armed = True
    await source._poll_once()

    # The stale (version 1) observation must NOT re-run the whole-store callback
    # for a bump the source already acknowledged locally as version 2.
    assert source_calls == []
    # The acknowledged version must not be rewound below the local ack.
    assert source._known_versions.get(NAMESPACE_RESET_CREDITS) == 2

    # A subsequent clean poll observes no new change and stays quiet.
    await source._poll_once()
    assert source_calls == []
    assert source._known_versions.get(NAMESPACE_RESET_CREDITS) == 2
