from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.load_balancer as balancer_module
from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy._service.http_bridge import request_submit as submit_module
from app.modules.proxy.cap_partitioning import CapPartition
from app.modules.proxy.load_balancer import AccountConcurrencyCaps, LoadBalancer
from tests.unit.test_http_bridge_idle_leases import _make_bridge_session

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("key_id", [None, "key-caps"])
@pytest.mark.parametrize(
    ("configured_cap", "replicas", "rank", "occupied_count", "expected_cap", "admitted"),
    [
        (128, 1, 0, 8, 128, True),
        (2, 1, 0, 2, 2, False),
        (0, 3, 2, 12, 0, True),
        (None, 1, 0, 8, 8, False),
        (5, 3, 0, 2, 2, False),
        (5, 3, 1, 1, 2, True),
        (5, 3, 2, 1, 1, False),
    ],
)
async def test_reacquire_uses_effective_snapshot_caps(
    monkeypatch, key_id, configured_cap, replicas, rank, occupied_count, expected_cap, admitted
):
    dashboard = SimpleNamespace(
        proxy_account_stream_limit=configured_cap,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
        proxy_account_lease_ttl_seconds=123,
    )
    get_settings = AsyncMock(return_value=dashboard)
    monkeypatch.setattr(submit_module, "_service_get_settings_cache", lambda: SimpleNamespace(get=get_settings))
    monkeypatch.setattr(balancer_module, "get_cap_partition", lambda: CapPartition(replicas, rank))
    balancer = LoadBalancer(cast(Any, None))
    service = SimpleNamespace(_load_balancer=balancer)
    session = _make_bridge_session(api_key_id=key_id)
    mixin = submit_module._HTTPBridgeRequestSubmitMixin
    occupied = []
    try:
        for _ in range(occupied_count):
            lease = await balancer.acquire_account_lease(
                session.account.id,
                kind="stream",
                concurrency_caps=AccountConcurrencyCaps(response_create_limit=4, stream_limit=0),
            )
            assert lease is not None
            occupied.append(lease)
        snapshot = await mixin._http_bridge_reacquire_snapshot(service, session)
        get_settings.assert_awaited_once()
        assert snapshot.concurrency_caps.stream_limit == expected_cap
        assert snapshot.routing_tunables.lease_ttl_seconds == 123
        get_settings.side_effect = AssertionError("snapshot must be used without I/O under pending_lock")
        async with session.pending_lock:
            if admitted:
                await mixin._ensure_http_bridge_session_stream_lease_locked(service, session, snapshot=snapshot)
                assert session.account_lease is not None
                assert session.account_lease.api_key_id == key_id
            else:
                with pytest.raises(ProxyResponseError) as refused:
                    await mixin._ensure_http_bridge_session_stream_lease_locked(service, session, snapshot=snapshot)
                assert refused.value.status_code == 429
                assert refused.value.payload["error"]["code"] == "account_stream_cap"
                assert session.account_lease is None
        assert await balancer.account_pressure_snapshot(session.account.id) == (0, occupied_count + int(admitted), 0.0)
    finally:
        await mixin._maybe_release_idle_http_bridge_session_lease(service, session)
        for lease in occupied:
            await balancer.release_account_lease(lease)
    assert await balancer.account_pressure_snapshot(session.account.id) == (0, 0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("key_id", [None, "key-caps"])
async def test_idle_reacquire_observes_dashboard_cap_changes(monkeypatch, key_id):
    dashboard = SimpleNamespace(proxy_account_stream_limit=1)
    get_settings = AsyncMock(return_value=dashboard)
    monkeypatch.setattr(submit_module, "_service_get_settings_cache", lambda: SimpleNamespace(get=get_settings))
    balancer = LoadBalancer(cast(Any, None))
    service = SimpleNamespace(_load_balancer=balancer)
    session = _make_bridge_session(api_key_id=key_id)
    mixin = submit_module._HTTPBridgeRequestSubmitMixin
    occupied = await balancer.acquire_account_lease(session.account.id, kind="stream")
    assert occupied is not None
    try:
        for limit, admitted in ((1, False), (2, True), (0, True), (1, False), (None, True)):
            dashboard.proxy_account_stream_limit = limit
            snapshot = await mixin._http_bridge_reacquire_snapshot(service, session)
            async with session.pending_lock:
                if admitted:
                    await mixin._ensure_http_bridge_session_stream_lease_locked(service, session, snapshot=snapshot)
                else:
                    with pytest.raises(ProxyResponseError) as refused:
                        await mixin._ensure_http_bridge_session_stream_lease_locked(service, session, snapshot=snapshot)
                    assert refused.value.payload["error"]["code"] == "account_stream_cap"
            await mixin._maybe_release_idle_http_bridge_session_lease(service, session)
            assert session.account_lease is None
            assert await balancer.account_pressure_snapshot(session.account.id) == (0, 1, 0.0)
        assert get_settings.await_count == 5
    finally:
        await balancer.release_account_lease(occupied)
    assert await balancer.account_pressure_snapshot(session.account.id) == (0, 0, 0.0)
