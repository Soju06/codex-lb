from __future__ import annotations

from contextlib import nullcontext
from typing import cast

import pytest

from app.core import shutdown as shutdown_module
from app.core.clients.proxy import ProxyResponseError
from app.modules.proxy.repo_bundle import ProxyRepoFactory
from app.modules.proxy.service import ProxyService, _AffinityPolicy, _HTTPBridgeSessionKey

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_new_sessions_rejected_during_drain() -> None:
    assert hasattr(shutdown_module, "is_bridge_drain_active")
    assert hasattr(shutdown_module, "set_bridge_drain_active")
    assert callable(shutdown_module.is_bridge_drain_active)
    assert callable(shutdown_module.set_bridge_drain_active)

    shutdown_module.set_bridge_drain_active(False)
    assert not shutdown_module.is_bridge_drain_active()

    service = ProxyService(repo_factory=cast(ProxyRepoFactory, nullcontext()))
    key = _HTTPBridgeSessionKey("request", "drain-test", None)

    shutdown_module.set_bridge_drain_active(True)
    assert shutdown_module.is_bridge_drain_active()

    with pytest.raises(ProxyResponseError) as exc_info:
        await service._get_or_create_http_bridge_session(
            key,
            headers={},
            affinity=_AffinityPolicy(),
            api_key=None,
            request_model=None,
            idle_ttl_seconds=30.0,
            max_sessions=16,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.payload["error"].get("code") == "bridge_drain_active"

    shutdown_module.set_bridge_drain_active(False)


def test_two_instances_same_key_converges() -> None:
    from app.core.balancer.rendezvous_hash import select_node

    ring = ["pod-a", "pod-b", "pod-c"]
    hash_input = "prompt_cache:my-cache-key:api-key-123"

    owner_from_pod_a = select_node(hash_input, ring)
    owner_from_pod_b = select_node(hash_input, ring)

    assert owner_from_pod_a == owner_from_pod_b, "Both pods must select the same owner for the same key"
    assert owner_from_pod_a in ring


def test_scale_up_minimal_key_remapping() -> None:
    from app.core.balancer.rendezvous_hash import select_node

    ring_5 = ["pod-a", "pod-b", "pod-c", "pod-d", "pod-e"]
    ring_6 = [*ring_5, "pod-f"]

    keyspace = [f"prompt_cache:key-{i}:api-key" for i in range(1000)]

    before = {k: select_node(k, ring_5) for k in keyspace}
    after = {k: select_node(k, ring_6) for k in keyspace}

    remapped = sum(1 for k in keyspace if before[k] != after[k])

    assert remapped <= 200, (
        f"Expected ≤20% remapping on scale-up (≤200/1000), got {remapped}/1000. "
        "This indicates modulo hashing is being used instead of rendezvous hash."
    )


def test_graceful_fallback_under_mismatch() -> None:
    import inspect

    from app.modules.proxy import service as proxy_module

    source = inspect.getsource(proxy_module)

    assert "owner_mismatch_graceful_fallback" in source, (
        "Expected 'owner_mismatch_graceful_fallback' event — graceful fallback not implemented"
    )

    lines = source.split("\n")
    for i, line in enumerate(lines):
        if "bridge_instance_mismatch" in line and "raise" in line:
            pytest.fail(
                f"Found 409 raise at line {i + 1}: {line.strip()}\nGraceful fallback should not raise 409 on mismatch."
            )


@pytest.mark.asyncio
async def test_ring_membership_stale_heartbeat_excluded() -> None:
    from datetime import timedelta

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.utils.time import utcnow
    from app.db.models import Base, BridgeRingMember
    from app.modules.proxy.ring_membership import RingMembershipService

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def get_session() -> AsyncSession:
        return session_maker()

    service = RingMembershipService(get_session)

    await service.register("pod-fresh")

    async with session_maker() as session:
        stale = BridgeRingMember(
            id="stale-id",
            instance_id="pod-stale",
            registered_at=utcnow() - timedelta(seconds=300),
            last_heartbeat_at=utcnow() - timedelta(seconds=200),
        )
        session.add(stale)
        await session.commit()

    active = await service.list_active(stale_threshold_seconds=120)

    assert "pod-fresh" in active, "Fresh pod should be in active ring"
    assert "pod-stale" not in active, "Stale pod should be excluded from active ring"

    await engine.dispose()
