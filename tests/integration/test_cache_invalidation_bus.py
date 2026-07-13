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
    NAMESPACE_SETTINGS,
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
