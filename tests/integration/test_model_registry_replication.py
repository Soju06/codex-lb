"""Two-replica (two instances over one database) model-registry replication tests.

Replica A ("leader") is simulated with a standalone ``ModelRegistry`` plus the
store's persist path; replica B ("follower") is the process-global registry
that serves this app instance, reconciled via the cache-invalidation bus or
the non-leader refresh-tick backstop.
"""

from __future__ import annotations

import dataclasses
import json
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.core.cache.invalidation as invalidation_module
import app.core.openai.model_refresh_scheduler as scheduler_module
from app.core.cache.invalidation import (
    NAMESPACE_ACCOUNT_SELECTION,
    NAMESPACE_MODEL_REGISTRY,
    CacheInvalidationPoller,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import (
    ModelRegistry,
    ModelRegistryExport,
    ModelRegistrySnapshot,
    ReasoningLevel,
    UpstreamModel,
    get_model_registry,
)
from app.core.openai.model_registry_store import (
    SCHEMA_VERSION,
    encode_registry_export,
    persist_registry_snapshot,
    reconcile_model_registry_from_store,
)
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus, CacheInvalidation, ModelRegistrySnapshotRecord
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

REPLICA_SLUG = "gpt-replica-fresh"


def _make_upstream_model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=272000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="balanced"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=True,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw={"visibility": "list"},
    )


def _handcrafted_snapshot() -> ModelRegistrySnapshot:
    models = {
        REPLICA_SLUG: _make_upstream_model(REPLICA_SLUG),
        "gpt-pro-only": _make_upstream_model("gpt-pro-only"),
    }
    return ModelRegistrySnapshot(
        models=models,
        model_plans={REPLICA_SLUG: frozenset({"plus", "pro"}), "gpt-pro-only": frozenset({"pro"})},
        plan_models={"plus": frozenset({REPLICA_SLUG}), "pro": frozenset(models)},
        model_service_tier_plans={},
        model_service_tier_accounts={},
        account_plans={"acc-1": "pro"},
        fetched_at=time.monotonic(),
        model_accounts={},
        account_catalogs_authoritative=False,
        bootstrap_floor_active=False,
        suppressed_model_slugs=frozenset({"gpt-withdrawn"}),
    )


async def _leader_persist(export: ModelRegistryExport, *, leader_id: str = "replica-a") -> str:
    """Persist replica A's registry state exactly as the leader scheduler does."""
    encoded = encode_registry_export(export)
    async with SessionLocal() as session:
        await persist_registry_snapshot(session, encoded=encoded, leader_id=leader_id)
    return encoded.content_hash


async def _refreshed_leader_export() -> ModelRegistryExport:
    leader_registry = ModelRegistry(ttl_seconds=300.0)
    models = [_make_upstream_model(REPLICA_SLUG)]
    await leader_registry.update({"plus": models, "pro": models})
    return await leader_registry.export_state()


async def _snapshot_row() -> ModelRegistrySnapshotRecord | None:
    async with SessionLocal() as session:
        return await session.scalar(select(ModelRegistrySnapshotRecord).where(ModelRegistrySnapshotRecord.id == 1))


async def _model_registry_bus_version() -> int | None:
    async with SessionLocal() as session:
        return await session.scalar(
            select(CacheInvalidation.version).where(CacheInvalidation.namespace == NAMESPACE_MODEL_REGISTRY)
        )


async def _stale_leader_export() -> ModelRegistryExport:
    max_age = get_settings().model_registry_snapshot_max_age_seconds
    stale_snapshot = dataclasses.replace(_handcrafted_snapshot(), fetched_at=time.monotonic() - (max_age + 3600))
    return ModelRegistryExport(snapshot=stale_snapshot, metadata_models=None)


async def _add_active_account(account_id: str, *, plan_type: str = "pro") -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type=plan_type,
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()


class _StubLeaderElection:
    def __init__(self, *, leader: bool) -> None:
        self.leader = leader

    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object | None:
        """Mirror the real gate: run the body only while leader, else no-op.

        When ``leader`` is true the guarded body is awaited and its result
        returned (bypassing the DB lease/heartbeat); when false the body is
        never run and ``None`` is returned, matching ``LeaderElection``.
        """
        if not self.leader:
            return None
        return await fn()


async def test_bus_propagated_refresh_is_visible_on_follower_v1_models(async_client) -> None:
    """A leader refresh on replica A must show up on replica B's /v1/models via
    the cache-invalidation bus, without B ever fetching upstream."""
    follower_poller = CacheInvalidationPoller(SessionLocal)
    follower_poller.on_invalidation(NAMESPACE_MODEL_REGISTRY, reconcile_model_registry_from_store)
    await follower_poller._poll_once()  # baseline versions before the leader refresh

    before = await async_client.get("/v1/models")
    assert before.status_code == 200
    assert REPLICA_SLUG not in {model["id"] for model in before.json()["data"]}

    content_hash = await _leader_persist(await _refreshed_leader_export())
    leader_poller = CacheInvalidationPoller(SessionLocal)
    await leader_poller.bump(NAMESPACE_MODEL_REGISTRY)

    await follower_poller._poll_once()

    assert get_model_registry().applied_content_hash == content_hash
    after = await async_client.get("/v1/models")
    assert after.status_code == 200
    assert REPLICA_SLUG in {model["id"] for model in after.json()["data"]}


async def test_follower_enforces_suppression_and_plan_gating(db_setup) -> None:
    del db_setup
    await _leader_persist(ModelRegistryExport(snapshot=_handcrafted_snapshot(), metadata_models=None))

    applied = await reconcile_model_registry_from_store()

    assert applied is True
    registry = get_model_registry()
    assert registry.is_suppressed_model("gpt-withdrawn") is True
    assert registry.plan_types_for_model("gpt-pro-only") == frozenset({"pro"})
    assert registry.plan_types_for_model(REPLICA_SLUG) == frozenset({"plus", "pro"})


async def test_catalog_clear_propagates_to_follower(async_client) -> None:
    follower_poller = CacheInvalidationPoller(SessionLocal)
    follower_poller.on_invalidation(NAMESPACE_MODEL_REGISTRY, reconcile_model_registry_from_store)

    await _leader_persist(await _refreshed_leader_export())
    await reconcile_model_registry_from_store()
    assert REPLICA_SLUG in {m["id"] for m in (await async_client.get("/v1/models")).json()["data"]}
    await follower_poller._poll_once()  # baseline

    cleared_hash = await _leader_persist(ModelRegistryExport(snapshot=None, metadata_models=None))
    leader_poller = CacheInvalidationPoller(SessionLocal)
    await leader_poller.bump(NAMESPACE_MODEL_REGISTRY)
    await follower_poller._poll_once()

    registry = get_model_registry()
    assert registry.applied_content_hash == cleared_hash
    assert registry.get_snapshot() is None
    slugs = {m["id"] for m in (await async_client.get("/v1/models")).json()["data"]}
    assert REPLICA_SLUG not in slugs
    assert "gpt-5.2" in slugs  # bootstrap floor restored


async def test_lost_bump_converges_via_non_leader_tick_without_upstream_fetch(db_setup, monkeypatch) -> None:
    del db_setup
    content_hash = await _leader_persist(await _refreshed_leader_export())
    # No bump: simulate the documented bump() swallow-on-failure.

    fetch_calls: list[str] = []

    async def _fail_fetch(*args, **kwargs):  # pragma: no cover - must never run
        fetch_calls.append("fetch")
        raise AssertionError("non-leader tick must not fetch upstream")

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _StubLeaderElection(leader=False))
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _fail_fetch)
    monkeypatch.setattr(scheduler_module, "fetch_models_for_plan", _fail_fetch)

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    registry = get_model_registry()
    assert registry.applied_content_hash == content_hash
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert REPLICA_SLUG in snapshot.models
    assert fetch_calls == []


async def test_non_leader_tick_skips_already_applied_snapshot(db_setup, monkeypatch) -> None:
    del db_setup
    await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    # A second reconcile (next tick, unchanged header) must be a no-op.
    assert await reconcile_model_registry_from_store() is False


async def test_leader_refresh_persists_then_bumps(db_setup, monkeypatch) -> None:
    del db_setup
    await _add_active_account("acc-leader")

    model = _make_upstream_model(REPLICA_SLUG)

    async def _stub_fetch(candidates, encryptor, accounts_repo=None):
        return scheduler_module._FetchResult(models=[model], account_models={"acc-leader": ("pro", [model])})

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _StubLeaderElection(leader=True))
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _stub_fetch)
    monkeypatch.setattr(invalidation_module, "_poller", CacheInvalidationPoller(SessionLocal))

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    row = await _snapshot_row()
    assert row is not None
    assert row.schema_version == SCHEMA_VERSION
    assert row.leader_id == get_settings().http_responses_session_bridge_instance_id
    assert get_model_registry().applied_content_hash == row.content_hash
    assert (await _model_registry_bus_version() or 0) >= 1


async def test_leader_persist_failure_keeps_refreshed_catalog(db_setup, monkeypatch) -> None:
    del db_setup
    await _add_active_account("acc-leader")

    model = _make_upstream_model(REPLICA_SLUG)

    async def _stub_fetch(candidates, encryptor, accounts_repo=None):
        return scheduler_module._FetchResult(models=[model], account_models={"acc-leader": ("pro", [model])})

    async def _failing_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _StubLeaderElection(leader=True))
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _stub_fetch)
    monkeypatch.setattr(scheduler_module, "persist_registry_snapshot", _failing_persist)
    monkeypatch.setattr(invalidation_module, "_poller", CacheInvalidationPoller(SessionLocal))

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()  # must not raise

    registry = get_model_registry()
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert REPLICA_SLUG in snapshot.models
    # The in-memory state diverges from the store, so the applied-hash marker
    # must be reset for later reconciles to converge back to the store.
    assert registry.applied_content_hash is None
    assert await _model_registry_bus_version() is None
    assert await _snapshot_row() is None


async def test_former_leader_reconverges_from_store_after_persist_failure(db_setup, monkeypatch) -> None:
    """A replica that applied persisted hash H, then won leadership, refreshed
    locally, and failed to persist must reconcile back to the store's snapshot
    once it loses leadership (instead of serving the unpublished catalog)."""
    del db_setup
    store_hash = await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    registry = get_model_registry()
    assert registry.applied_content_hash == store_hash

    await _add_active_account("acc-leader")
    local_model = _make_upstream_model("gpt-leader-local")

    async def _stub_fetch(candidates, encryptor, accounts_repo=None):
        return scheduler_module._FetchResult(
            models=[local_model],
            account_models={"acc-leader": ("pro", [local_model])},
        )

    async def _failing_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    election = _StubLeaderElection(leader=True)
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: election)
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _stub_fetch)
    monkeypatch.setattr(scheduler_module, "persist_registry_snapshot", _failing_persist)

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert "gpt-leader-local" in snapshot.models
    assert registry.applied_content_hash is None

    # Lose leadership: the non-leader tick backstop must converge back to the
    # persisted snapshot the other replicas are serving.
    election.leader = False
    await scheduler._refresh_once()

    assert registry.applied_content_hash == store_hash
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert REPLICA_SLUG in snapshot.models
    assert "gpt-leader-local" not in snapshot.models


async def test_leader_drops_stale_snapshot_when_all_fetches_fail(db_setup, monkeypatch) -> None:
    """Under a prolonged upstream outage every leader fetch fails, so the leader
    never advances the persisted refreshed_at. Once the stored row ages past the
    staleness cap the leader must reconcile-drop to the bootstrap floor (matching
    followers) instead of serving its stale in-memory catalog indefinitely."""
    del db_setup
    store_hash = await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    registry = get_model_registry()
    assert registry.get_snapshot() is not None
    assert registry.applied_content_hash == store_hash

    # The leader stopped confirming the catalog long enough that the only row
    # has aged past the staleness cap.
    max_age = get_settings().model_registry_snapshot_max_age_seconds
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET refreshed_at = :ts WHERE id = 1"),
            {"ts": utcnow() - timedelta(seconds=max_age + 3600)},
        )
        await session.commit()

    await _add_active_account("acc-leader")
    fetch_attempts: list[str] = []

    async def _all_fetches_fail(candidates, encryptor, accounts_repo=None):
        fetch_attempts.append("attempt")
        return None  # every plan failed

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _StubLeaderElection(leader=True))
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _all_fetches_fail)

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    # It took the leader path (attempted a fetch) then dropped to the floor.
    assert fetch_attempts == ["attempt"]
    assert registry.get_snapshot() is None
    assert registry.applied_content_hash is None
    # No successful refresh, so the leader did not rewrite/advance the store row.
    row = await _snapshot_row()
    assert row is not None
    assert row.content_hash == store_hash


async def test_leader_keeps_fresh_snapshot_when_all_fetches_fail(db_setup, monkeypatch) -> None:
    """When the stored row is still fresh, an all-fetch-failed leader tick must
    keep serving the applied catalog (the reconcile is a no-op), not drop it."""
    del db_setup
    store_hash = await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    registry = get_model_registry()
    assert registry.get_snapshot() is not None

    await _add_active_account("acc-leader")

    async def _all_fetches_fail(candidates, encryptor, accounts_repo=None):
        return None

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _StubLeaderElection(leader=True))
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _all_fetches_fail)

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    assert registry.applied_content_hash == store_hash
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert REPLICA_SLUG in snapshot.models


async def test_former_leader_drops_unpublished_snapshot_when_store_is_empty(db_setup, monkeypatch) -> None:
    """The first-ever leader refresh succeeds locally but its persist fails, so
    no snapshot row exists. Once leadership is lost, the non-leader tick must
    drop the unpublished catalog and revert to the bootstrap floor the other
    replicas are serving, instead of keeping it until a row appears."""
    del db_setup
    await _add_active_account("acc-leader")
    local_model = _make_upstream_model("gpt-leader-local")

    async def _stub_fetch(candidates, encryptor, accounts_repo=None):
        return scheduler_module._FetchResult(
            models=[local_model],
            account_models={"acc-leader": ("pro", [local_model])},
        )

    async def _failing_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    election = _StubLeaderElection(leader=True)
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: election)
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _stub_fetch)
    monkeypatch.setattr(scheduler_module, "persist_registry_snapshot", _failing_persist)

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    registry = get_model_registry()
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert "gpt-leader-local" in snapshot.models
    assert registry.applied_content_hash is None
    assert await _snapshot_row() is None

    # Lose leadership with the store still empty: the non-leader tick backstop
    # must drop the unpublished catalog so this replica converges with peers.
    election.leader = False
    await scheduler._refresh_once()

    assert registry.get_snapshot() is None
    assert registry.applied_content_hash is None

    # Subsequent ticks against the still-empty store are idempotent no-ops.
    assert await reconcile_model_registry_from_store() is False
    assert registry.get_snapshot() is None


async def test_former_leader_drops_unpublished_snapshot_when_store_is_expired(db_setup, monkeypatch) -> None:
    """A leader refreshes locally but its persist fails, so the only store row is
    the previously-published snapshot that has since aged past the staleness cap.
    Once leadership is lost, the non-leader tick must drop the unpublished
    catalog and revert to the bootstrap floor the other replicas are serving,
    not keep serving the leader-local catalog because it carries no applied hash."""
    del db_setup
    await _add_active_account("acc-leader")

    # A previously published row exists but is now expired (leader stopped
    # confirming it); other replicas have already reverted to the floor.
    await _leader_persist(await _refreshed_leader_export())
    max_age = get_settings().model_registry_snapshot_max_age_seconds
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET refreshed_at = :ts WHERE id = 1"),
            {"ts": utcnow() - timedelta(seconds=max_age + 3600)},
        )
        await session.commit()

    local_model = _make_upstream_model("gpt-leader-local")

    async def _stub_fetch(candidates, encryptor, accounts_repo=None):
        return scheduler_module._FetchResult(
            models=[local_model],
            account_models={"acc-leader": ("pro", [local_model])},
        )

    async def _failing_persist(*args, **kwargs):
        raise RuntimeError("database unavailable")

    election = _StubLeaderElection(leader=True)
    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: election)
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _stub_fetch)
    monkeypatch.setattr(scheduler_module, "persist_registry_snapshot", _failing_persist)

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()

    registry = get_model_registry()
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert "gpt-leader-local" in snapshot.models
    assert registry.applied_content_hash is None

    # Lose leadership with the store row still expired: the non-leader tick
    # backstop must drop the unpublished catalog so this replica converges.
    election.leader = False
    await scheduler._refresh_once()

    assert registry.get_snapshot() is None
    assert registry.applied_content_hash is None

    # Reconciling the same expired row again is a log-only no-op.
    assert await reconcile_model_registry_from_store() is False
    assert registry.get_snapshot() is None


async def test_unchanged_content_touches_refreshed_at_without_rewrite(db_setup) -> None:
    del db_setup
    export = await _refreshed_leader_export()
    assert export.snapshot is not None

    first = encode_registry_export(export)
    async with SessionLocal() as session:
        assert await persist_registry_snapshot(session, encoded=first, leader_id="replica-a") is True

    aged = dataclasses.replace(export.snapshot, fetched_at=time.monotonic())
    second = encode_registry_export(ModelRegistryExport(snapshot=aged, metadata_models=export.metadata_models))
    assert second.content_hash == first.content_hash
    async with SessionLocal() as session:
        assert await persist_registry_snapshot(session, encoded=second, leader_id="replica-b") is False

    row = await _snapshot_row()
    assert row is not None
    assert row.content_hash == first.content_hash
    assert row.leader_id == "replica-b"
    assert row.refreshed_at >= first.refreshed_at


async def test_startup_loads_persisted_snapshot_before_first_refresh(app_instance, monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    import app.main as main_module

    class _NoopScheduler:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    await _leader_persist(await _refreshed_leader_export())

    monkeypatch.setattr(get_settings(), "model_registry_enabled", True)
    # Keep the real refresh scheduler out of the way: the assertion is about
    # the catalog served *before* the first refresh tick.
    monkeypatch.setattr(main_module, "build_model_refresh_scheduler", lambda: _NoopScheduler())

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/v1/models")
    assert response.status_code == 200
    assert REPLICA_SLUG in {model["id"] for model in response.json()["data"]}


async def test_model_scheduler_starts_after_invalidation_poller_installed(app_instance, monkeypatch) -> None:
    """The first leader tick can persist-and-bump immediately; the global
    cache-invalidation poller must already be installed when the model
    scheduler starts or that bump is silently dropped."""
    import app.main as main_module
    from app.core.cache.invalidation import get_cache_invalidation_poller

    poller_installed_at_start: list[bool] = []

    class _ProbeScheduler:
        async def start(self) -> None:
            poller_installed_at_start.append(get_cache_invalidation_poller() is not None)

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(invalidation_module, "_poller", None)
    monkeypatch.setattr(get_settings(), "model_registry_enabled", True)
    monkeypatch.setattr(main_module, "build_model_refresh_scheduler", lambda: _ProbeScheduler())

    async with app_instance.router.lifespan_context(app_instance):
        pass

    assert poller_installed_at_start == [True]


async def test_prime_delivers_bump_that_lands_after_baseline(db_setup) -> None:
    """A bump recorded after prime() must fire the callback on the next poll
    rather than be absorbed as the poller's initial baseline. This is the
    startup reconcile-vs-poller race (especially the no-prior-row clear path):
    a leader persists-and-bumps between the one-shot reconcile and the poller's
    first tick, and the replica must still converge within the poll bound."""
    del db_setup
    calls: list[str] = []
    poller = CacheInvalidationPoller(SessionLocal)
    poller.on_invalidation(NAMESPACE_MODEL_REGISTRY, lambda: calls.append("cb"))

    # No model_registry row exists yet when the baseline is recorded.
    await poller.prime()
    assert await _model_registry_bus_version() is None
    # A leader persists-and-bumps in the startup window.
    await poller.bump(NAMESPACE_MODEL_REGISTRY)
    # The first background tick after priming must deliver the callback.
    await poller._poll_once()

    assert calls == ["cb"]


async def test_prime_delivers_bump_after_existing_baseline(db_setup) -> None:
    """The same guarantee holds when a snapshot row already exists at baseline
    time: a version that advances past the primed baseline fires the callback."""
    del db_setup
    calls: list[str] = []
    poller = CacheInvalidationPoller(SessionLocal)
    poller.on_invalidation(NAMESPACE_MODEL_REGISTRY, lambda: calls.append("cb"))

    # A row already exists (version 1) before the baseline is recorded.
    await poller.bump(NAMESPACE_MODEL_REGISTRY)
    await poller.prime()
    # A later leader bump advances the version past the recorded baseline.
    await poller.bump(NAMESPACE_MODEL_REGISTRY)
    await poller._poll_once()

    assert calls == ["cb"]


async def test_startup_primes_poll_baseline_before_reconcile(app_instance, monkeypatch) -> None:
    """The poll baseline must be seeded before the one-shot startup reconcile
    (and both before the scheduler starts), so a leader bump landing in that
    window is delivered as an invalidation instead of the initial baseline."""
    import app.core.openai.model_registry_store as store_module
    import app.main as main_module

    order: list[str] = []
    real_prime = CacheInvalidationPoller.prime

    async def _tracked_prime(self: CacheInvalidationPoller) -> None:
        order.append("prime")
        await real_prime(self)

    async def _tracked_reconcile() -> bool:
        order.append("reconcile")
        return False

    class _ProbeScheduler:
        async def start(self) -> None:
            order.append("scheduler_start")

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(invalidation_module, "_poller", None)
    monkeypatch.setattr(get_settings(), "model_registry_enabled", True)
    monkeypatch.setattr(CacheInvalidationPoller, "prime", _tracked_prime)
    monkeypatch.setattr(store_module, "reconcile_model_registry_from_store", _tracked_reconcile)
    monkeypatch.setattr(main_module, "build_model_refresh_scheduler", lambda: _ProbeScheduler())

    async with app_instance.router.lifespan_context(app_instance):
        pass

    assert "prime" in order and "reconcile" in order and "scheduler_start" in order
    assert order.index("prime") < order.index("reconcile") < order.index("scheduler_start")


async def test_snapshot_older_than_staleness_cap_is_ignored(db_setup) -> None:
    del db_setup
    await _leader_persist(await _stale_leader_export())

    applied = await reconcile_model_registry_from_store()

    assert applied is False
    assert get_model_registry().get_snapshot() is None


async def test_expired_store_entry_drops_already_applied_snapshot(db_setup) -> None:
    """A follower that applied a snapshot while fresh must revert to the
    bootstrap floor once the store entry exceeds the staleness cap (leader
    stopped confirming the catalog), not keep serving it indefinitely."""
    del db_setup
    await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    registry = get_model_registry()
    assert registry.get_snapshot() is not None

    max_age = get_settings().model_registry_snapshot_max_age_seconds
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET refreshed_at = :ts WHERE id = 1"),
            {"ts": utcnow() - timedelta(seconds=max_age + 3600)},
        )
        await session.commit()

    assert await reconcile_model_registry_from_store() is False
    assert registry.get_snapshot() is None  # bootstrap floor restored
    assert registry.applied_content_hash is None
    # Idempotent: reconciling the same expired row again is a log-only no-op.
    assert await reconcile_model_registry_from_store() is False
    assert registry.get_snapshot() is None


async def test_schema_version_skew_is_ignored_without_error(db_setup) -> None:
    del db_setup
    await _leader_persist(await _refreshed_leader_export())
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET schema_version = :v WHERE id = 1"),
            {"v": SCHEMA_VERSION + 1},
        )
        await session.commit()

    applied = await reconcile_model_registry_from_store()

    assert applied is False
    assert get_model_registry().get_snapshot() is None


async def test_stale_snapshot_replaced_after_next_leader_refresh(db_setup) -> None:
    del db_setup
    await _leader_persist(await _stale_leader_export())
    assert await reconcile_model_registry_from_store() is False

    await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    snapshot = get_model_registry().get_snapshot()
    assert snapshot is not None
    assert REPLICA_SLUG in snapshot.models


async def test_apply_does_not_propagate_account_selection_bump(db_setup, monkeypatch) -> None:
    """Applying the leader's snapshot only needs a local selection-cache clear:
    the leader's refresh already bumped ``model_registry`` (reaching every
    replica) and each replica clears its own selection cache on apply. A
    propagating clear here would make every follower durably re-bump
    ``account_selection`` on the next poll, amplifying bus traffic for no
    peer-visible effect."""
    del db_setup
    poller = CacheInvalidationPoller(SessionLocal)
    monkeypatch.setattr(invalidation_module, "_poller", poller)

    await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True

    assert NAMESPACE_ACCOUNT_SELECTION not in poller._pending_bumps


async def test_drop_to_floor_does_not_propagate_account_selection_bump(db_setup, monkeypatch) -> None:
    """Reverting to the bootstrap floor when the store row disappears is a local
    convergence step every replica performs independently off the same observed
    store state, so it must not re-bump ``account_selection`` either."""
    del db_setup
    poller = CacheInvalidationPoller(SessionLocal)
    monkeypatch.setattr(invalidation_module, "_poller", poller)

    await _leader_persist(await _refreshed_leader_export())
    assert await reconcile_model_registry_from_store() is True
    assert get_model_registry().get_snapshot() is not None
    poller._pending_bumps.clear()

    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM model_registry_snapshot WHERE id = 1"))
        await session.commit()

    assert await reconcile_model_registry_from_store() is False
    assert get_model_registry().get_snapshot() is None  # reverted to floor
    assert NAMESPACE_ACCOUNT_SELECTION not in poller._pending_bumps


async def test_load_failure_is_swallowed_by_default_but_raises_when_requested(db_setup) -> None:
    """A transient load failure (malformed payload) must keep the current
    in-memory state and, by default, be swallowed. The invalidation-callback
    variant re-raises so the poller can leave the version unacknowledged."""
    del db_setup
    await _leader_persist(await _refreshed_leader_export())
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET payload = :p WHERE id = 1"),
            {"p": "not-json"},
        )
        await session.commit()

    # Default: failure is swallowed, no snapshot applied.
    assert await reconcile_model_registry_from_store() is False
    assert get_model_registry().get_snapshot() is None

    # Callback variant: the failure surfaces so the poller does not ACK.
    with pytest.raises(Exception):
        await reconcile_model_registry_from_store(raise_on_error=True)
    assert get_model_registry().get_snapshot() is None


async def test_invalidation_callback_load_failure_leaves_version_unacked_and_retries(db_setup) -> None:
    """Product-path regression: as the ``model_registry`` invalidation callback,
    a transient load failure must NOT acknowledge the observed version so the
    poller retries on the next cycle instead of stranding the replica on the
    stale catalog until the scheduler backstop."""
    del db_setup
    poller = CacheInvalidationPoller(SessionLocal)
    poller.on_invalidation(
        NAMESPACE_MODEL_REGISTRY,
        lambda: reconcile_model_registry_from_store(raise_on_error=True),
    )
    await poller.prime()  # baseline before the leader publishes anything

    content_hash = await _leader_persist(await _refreshed_leader_export())
    async with SessionLocal() as session:
        good_payload = await session.scalar(
            select(ModelRegistrySnapshotRecord.payload).where(ModelRegistrySnapshotRecord.id == 1)
        )
        await session.execute(
            text("UPDATE model_registry_snapshot SET payload = :p WHERE id = 1"),
            {"p": "not-json"},
        )
        await session.commit()
    leader_poller = CacheInvalidationPoller(SessionLocal)
    await leader_poller.bump(NAMESPACE_MODEL_REGISTRY)

    # First poll: the callback raises, so the version stays unacknowledged.
    await poller._poll_once()
    assert get_model_registry().get_snapshot() is None
    assert NAMESPACE_MODEL_REGISTRY not in poller._known_versions

    # The transient failure clears (payload readable again). A subsequent poll
    # retries the SAME (never-acked) version and converges without a new bump.
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET payload = :p WHERE id = 1"),
            {"p": good_payload},
        )
        await session.commit()

    await poller._poll_once()
    assert get_model_registry().applied_content_hash == content_hash
    assert get_model_registry().get_snapshot() is not None


async def test_malformed_set_backed_field_leaves_version_unacked_and_not_applied(db_setup) -> None:
    """Product-path regression for the P2 finding: a payload that is valid JSON
    but has a wrong-typed set-backed field (``model_plans`` value as a dict
    where a list of slugs is expected) MUST reject on decode rather than
    silently dropping the entry. As the invalidation callback this leaves the
    ``model_registry`` version unacknowledged and applies NO partial catalog."""
    del db_setup
    poller = CacheInvalidationPoller(SessionLocal)
    poller.on_invalidation(
        NAMESPACE_MODEL_REGISTRY,
        lambda: reconcile_model_registry_from_store(raise_on_error=True),
    )
    await poller.prime()

    await _leader_persist(await _refreshed_leader_export())
    async with SessionLocal() as session:
        good_payload = await session.scalar(
            select(ModelRegistrySnapshotRecord.payload).where(ModelRegistrySnapshotRecord.id == 1)
        )
        assert good_payload is not None
        document = json.loads(good_payload)
        # Corrupt one set-backed field into a dict; previously the decode
        # comprehension silently dropped it and applied a plan-gating-less model.
        document["snapshot"]["model_plans"][REPLICA_SLUG] = {"gpt-x": "pro"}
        await session.execute(
            text("UPDATE model_registry_snapshot SET payload = :p WHERE id = 1"),
            {"p": json.dumps(document)},
        )
        await session.commit()
    leader_poller = CacheInvalidationPoller(SessionLocal)
    await leader_poller.bump(NAMESPACE_MODEL_REGISTRY)

    # The callback raises on the malformed field, so the version stays unacked
    # and no partial catalog is applied.
    await poller._poll_once()
    assert get_model_registry().get_snapshot() is None
    assert NAMESPACE_MODEL_REGISTRY not in poller._known_versions

    # Once the leader republishes a good payload, the never-acked version
    # converges on the next poll without a new bump.
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET payload = :p WHERE id = 1"),
            {"p": good_payload},
        )
        await session.commit()
    await poller._poll_once()
    assert get_model_registry().get_snapshot() is not None


async def test_reviving_expired_unchanged_snapshot_returns_changed(db_setup) -> None:
    """Unit regression: an unchanged-content persist against a store row that
    has aged past the staleness cap must report ``True`` (bump-worthy). Followers
    cleared their local registry and reset their applied-hash marker when the row
    expired, so an unchanged-content revival still needs a bump for them to
    re-apply within the poll bound rather than the scheduler backstop."""
    del db_setup
    export = await _refreshed_leader_export()
    assert export.snapshot is not None

    first = encode_registry_export(export)
    async with SessionLocal() as session:
        assert await persist_registry_snapshot(session, encoded=first, leader_id="replica-a") is True

    # Age the stored row past the staleness cap so followers would have dropped
    # to the bootstrap floor before the leader revives it.
    max_age = get_settings().model_registry_snapshot_max_age_seconds
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET refreshed_at = :ts WHERE id = 1"),
            {"ts": utcnow() - timedelta(seconds=max_age + 3600)},
        )
        await session.commit()

    aged = dataclasses.replace(export.snapshot, fetched_at=time.monotonic())
    second = encode_registry_export(ModelRegistryExport(snapshot=aged, metadata_models=export.metadata_models))
    assert second.content_hash == first.content_hash
    async with SessionLocal() as session:
        # Same content, but the revived expired row is bump-worthy.
        assert await persist_registry_snapshot(session, encoded=second, leader_id="replica-b") is True

    row = await _snapshot_row()
    assert row is not None
    assert row.content_hash == first.content_hash
    # The revival advanced refreshed_at back inside the staleness cap.
    revived_age = (utcnow() - to_utc_naive(row.refreshed_at)).total_seconds()
    assert revived_age < max_age


async def test_leader_revives_expired_snapshot_and_bumps_bus(db_setup, monkeypatch) -> None:
    """Product-path regression: after the stored row ages past the staleness cap,
    a leader refresh that fetches the SAME catalog bytes must still bump the
    ``model_registry`` bus so followers re-apply within the poll bound."""
    del db_setup
    await _add_active_account("acc-leader")
    model = _make_upstream_model(REPLICA_SLUG)

    async def _stub_fetch(candidates, encryptor, accounts_repo=None):
        return scheduler_module._FetchResult(models=[model], account_models={"acc-leader": ("pro", [model])})

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _StubLeaderElection(leader=True))
    monkeypatch.setattr(scheduler_module, "_fetch_with_failover", _stub_fetch)
    monkeypatch.setattr(invalidation_module, "_poller", CacheInvalidationPoller(SessionLocal))

    scheduler = scheduler_module.ModelRefreshScheduler(interval_seconds=300, enabled=True)
    await scheduler._refresh_once()
    version_after_first = await _model_registry_bus_version()
    assert version_after_first is not None and version_after_first >= 1

    # The stored row ages past the staleness cap (followers drop to the floor).
    max_age = get_settings().model_registry_snapshot_max_age_seconds
    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE model_registry_snapshot SET refreshed_at = :ts WHERE id = 1"),
            {"ts": utcnow() - timedelta(seconds=max_age + 3600)},
        )
        await session.commit()

    # A second leader tick refreshes the identical catalog; the revival must bump.
    await scheduler._refresh_once()
    version_after_revival = await _model_registry_bus_version()
    assert version_after_revival is not None and version_after_revival > version_after_first


async def test_prime_surfaces_baseline_read_failure(db_setup) -> None:
    """A failed baseline-priming read must raise (poller stays uninitialized) so
    startup degrades explicitly instead of silently continuing as if the baseline
    was recorded, which would let the first background poll absorb a peer bump."""
    del db_setup

    def _explode() -> AsyncSession:
        raise RuntimeError("baseline version read unavailable")

    poller = CacheInvalidationPoller(_explode)
    with pytest.raises(RuntimeError):
        await poller.prime()
    assert poller._poll_initialized is False
