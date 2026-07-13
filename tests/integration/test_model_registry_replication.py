"""Two-replica (two instances over one database) model-registry replication tests.

Replica A ("leader") is simulated with a standalone ``ModelRegistry`` plus the
store's persist path; replica B ("follower") is the process-global registry
that serves this app instance, reconciled via the cache-invalidation bus or
the non-leader refresh-tick backstop.
"""

from __future__ import annotations

import dataclasses
import time
from datetime import timedelta

import pytest
from sqlalchemy import select, text

import app.core.cache.invalidation as invalidation_module
import app.core.openai.model_refresh_scheduler as scheduler_module
from app.core.cache.invalidation import NAMESPACE_MODEL_REGISTRY, CacheInvalidationPoller
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
from app.core.utils.time import utcnow
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

    async def try_acquire(self) -> bool:
        return self.leader


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
