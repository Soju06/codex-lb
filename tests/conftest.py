from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

TEST_DB_DIR = Path(tempfile.mkdtemp(prefix="codex-lb-tests-"))
TEST_DB_PATH = TEST_DB_DIR / "codex-lb.db"

os.environ["CODEX_LB_DATABASE_URL"] = os.environ.get(
    "CODEX_LB_TEST_DATABASE_URL", f"sqlite+aiosqlite:///{TEST_DB_PATH}"
)
os.environ["CODEX_LB_UPSTREAM_BASE_URL"] = "https://example.invalid/backend-api"
# The HTTP responses session bridge is a request-path feature with a T4 env
# kill switch (see app/core/config/tiers.py). The suite runs on the raw
# upstream path by default; bridge suites opt in with explicit ``Settings``.
os.environ["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED"] = "false"
# Route-resolution caching is opt-in per test (cache-specific tests set a TTL
# explicitly); keeping it off preserves fresh-read semantics everywhere else.
os.environ["CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS"] = "0"
# NOTE: Background loops (model registry, sticky cleanup, quota planner,
# automations, auth guardian, usage refresh, ...) and leader election are
# intentionally NOT disabled via ``CODEX_LB_*_ENABLED`` env overrides here. They
# are default-enabled in production, and a global override would leak into every
# ``Settings()`` constructed anywhere in the suite — breaking production-default
# assertions such as test_settings_multi_replica.py — while silently turning
# into a no-op the day a toggle is constantized. Instead the ambient app
# lifespan's scheduler builders are replaced with no-ops by the autouse
# ``_disable_background_loop_schedulers`` fixture and its leader election by
# ``_disable_leader_election_startup`` (see their docstrings below).

from app.db.models import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.modules.auth_providers.seed import seed_default_auth_providers  # noqa: E402
from app.modules.dashboard_roles.seed import seed_preset_dashboard_roles  # noqa: E402


class _NoopScheduler:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


# Every background loop the app lifespan builds through an ``app.main``
# ``build_*_scheduler`` seam and then ``start()``s. The autouse
# ``_disable_background_loop_schedulers`` fixture swaps each builder for a
# ``_NoopScheduler`` factory so the ambient test lifespan never starts them; a
# scheduler that does real work in the suite (external GitHub/npm lookups,
# upstream usage polls, no-op planner decision rows, real-clock automation
# ticks racing a test's own due-now jobs) shows up as SQLite lock flakes and
# cross-test poisoning. tests/unit/test_background_loop_harness.py pins this
# tuple against the builders ``app.main`` actually imports, so adding a loop
# without classifying it here fails the suite instead of silently running.
# Tests that exercise a scheduler construct it directly or patch the builder
# themselves (e.g. test_otel, test_telemetry_consent,
# test_model_registry_replication) and keep working.
BACKGROUND_LOOP_BUILDERS: tuple[str, ...] = (
    "build_metadata_refresh_scheduler",
    "build_usage_refresh_scheduler",
    "build_model_refresh_scheduler",
    "build_sticky_session_cleanup_scheduler",
    "build_quota_planner_scheduler",
    "build_auth_guardian_scheduler",
    "build_automations_scheduler",
    "build_rate_limit_reset_credits_scheduler",
    "build_account_usage_rollup_scheduler",
    "build_data_retention_scheduler",
    "build_telemetry_scheduler",
    "build_account_deletion_scheduler",
)


class _NoopLeaderElection:
    """Stand-in for the leader-election singleton during the test app lifespan.

    Leader election is default-enabled in production and performs REAL SQLite
    writes (acquire / renew / release) on the shared single-writer test database
    via the app lifespan's release keeper. Left running, those renewal/release
    writes contend with unrelated integration tests' DB work and with schema
    teardown, surfacing as ``database is locked`` at setup/teardown.

    Rather than override the production default (which would leak into every
    ``Settings()`` and defeat the default-value unit tests), we scope the
    disabling to the app lifespan by swapping the resolved singleton for this
    no-op. It mirrors the module's own ``leader_election_enabled=False`` escape
    hatch — always "leader", body runs inline, keeper/release are no-ops — but
    without touching ``Settings`` and without any DB writes. Tests that exercise
    leader election construct their own ``LeaderElection`` instances with their
    own enabled settings (see tests/unit/test_leader_election.py and
    tests/integration/test_multi_replica.py) and are unaffected.
    """

    leader_id = "test-noop-leader"

    @property
    def is_leader(self) -> bool:
        return True

    async def try_acquire(self) -> bool:
        return True

    async def renew(self) -> bool:
        return True

    async def run_if_leader(self, fn):
        return await fn()

    def start_release_keeper(self) -> None:
        return None

    async def release(self) -> bool:
        return True


def _drop_test_migration_tables(sync_conn) -> None:
    sync_conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    sync_conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))


def _recreate_test_schema(sync_conn) -> None:
    _drop_test_migration_tables(sync_conn)
    Base.metadata.drop_all(sync_conn)
    Base.metadata.create_all(sync_conn)
    # Production seeds these through the migration and at startup; the test
    # schema is built with create_all, so seed the preset role rows here too.
    seed_preset_dashboard_roles(sync_conn)
    seed_default_auth_providers(sync_conn)


def _reset_test_database(sync_conn) -> None:
    _recreate_test_schema(sync_conn)


@pytest_asyncio.fixture
async def _reset_db_state():
    from app.db.session import close_db

    await close_db()
    async with engine.begin() as conn:
        await conn.run_sync(_reset_test_database)
    return True


async def _reap_leaked_http_bridge_recovery_settlement_tasks(app) -> None:
    """Cancel bridge retries before the fixture's database is reused.

    Recovery-settlement retries deliberately keep their durable owner fence
    alive with backoff that can exceed the app lifespan drain.  A test that
    exercises the fail-closed replay path can therefore leave one of those
    tasks on the session loop after lifespan teardown; its next retry then
    races ``_reset_db_state`` or the next lifespan's startup write against
    the same SQLite file.  The production retry contract stays unchanged —
    this is only the test boundary reclaiming work owned by this app instance.

    TestClient uses a private portal loop.  Tasks bound to that already-closed
    loop cannot be driven from this fixture's session loop, so only cancel and
    await tasks that belong to the current loop.
    """
    service = getattr(getattr(app, "state", None), "proxy_service", None)
    if service is None:
        return
    loop = asyncio.get_running_loop()
    tasks = [
        task
        for task in getattr(service, "_background_cleanup_tasks", ())
        if not task.done()
        and task.get_name().startswith("http-bridge-recovery-settlement-")
        and task.get_loop() is loop
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
        # Let the tracking callbacks discard the settled tasks before the
        # service becomes unreachable and the next fixture resets the DB.
        await asyncio.sleep(0)


@pytest_asyncio.fixture
async def app_instance(_reset_db_state, monkeypatch):
    del _reset_db_state
    import app.main as main_module

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    app = create_app()
    yield app
    await _reap_leaked_http_bridge_recovery_settlement_tasks(app)


@pytest.fixture(autouse=True)
def _disable_request_log_count_cache(monkeypatch):
    """Zero the request-log COUNT cache TTL so listing totals stay exact
    within a test. The TTL is a fixed constant in production (issue #1340
    phase 2); the cache-behavior test patches it back to a positive value."""
    import app.modules.request_logs.repository as logs_repository_module

    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _disable_account_usage_summary_cache(monkeypatch):
    """Zero the account request-usage summary cache TTL so listing summaries
    stay exact within a test. The TTL is a fixed constant in production;
    cache-behavior tests patch it back to a positive value."""
    import app.modules.accounts.repository as accounts_repository_module

    accounts_repository_module._clear_request_usage_summary_cache()
    monkeypatch.setattr(accounts_repository_module, "_SUMMARY_CACHE_TTL_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _disable_dashboard_trailing_demand_cache(monkeypatch):
    """Zero the weekly-pace trailing-demand cache TTL so dashboard pace
    figures stay exact within a test. The TTL is a fixed constant in
    production; cache-behavior tests patch it back to a positive value."""
    import app.modules.dashboard.repository as dashboard_repository_module

    dashboard_repository_module._clear_trailing_demand_cache()
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _disable_background_loop_schedulers(monkeypatch) -> tuple[str, ...]:
    """Replace every ambient app-lifespan background loop with a no-op.

    The lifespan in ``app.main`` resolves each ``build_*_scheduler`` name from
    its own module globals at startup, so patching those names is enough to
    keep the loops from ever starting — without touching ``Settings`` (unit
    tests still observe the real production defaults such as
    ``automations_scheduler_enabled is True``) and without depending on a
    ``CODEX_LB_*_ENABLED`` env override that would silently stop working once
    the toggle behind it is constantized. Returns the patched builder names so
    the coverage test can assert the seam is complete.
    """
    import app.main as main_module

    for builder_name in BACKGROUND_LOOP_BUILDERS:
        monkeypatch.setattr(main_module, builder_name, lambda: _NoopScheduler())
    return BACKGROUND_LOOP_BUILDERS


@pytest.fixture(autouse=True)
def _disable_request_path_usage_refresh(request, monkeypatch):
    """Turn the request-path usage refreshes into no-ops.

    Background usage refresh is always on in production (the env kill switch
    was constantized; issue #1340), and it is not only the scheduler's loop:
    ``UsageUpdater.refresh_accounts`` runs on account import and
    ``UsageUpdater.request_refresh`` after a streamed ``usage_limit_reached``.
    Left live, every imported account would fetch usage from the unreachable
    test upstream (measured 20-30 s per import). Tests that exercise the
    updater itself opt out with ``@pytest.mark.usage_refresh_request_path``.
    """
    if request.node.get_closest_marker("usage_refresh_request_path") is not None:
        return
    from app.modules.usage.updater import UsageUpdater

    async def _noop_refresh_accounts(
        self, accounts, latest_usage, *, own_singleflight_sessions=False, join_existing=None
    ):
        del self, accounts, latest_usage, own_singleflight_sessions, join_existing
        return False

    monkeypatch.setattr(UsageUpdater, "refresh_accounts", _noop_refresh_accounts)
    monkeypatch.setattr(UsageUpdater, "request_refresh", staticmethod(lambda account_id: None))


@pytest.fixture(autouse=True)
def _disable_leader_election_startup(monkeypatch):
    """Replace the ambient app-lifespan leader election with a no-op.

    Scoped exactly like the sibling ``_disable_background_loop_schedulers`` fixture:
    it swaps what ``get_leader_election()`` resolves to (both the reference the
    app lifespan imported into ``app.main`` and the source-module singleton
    every scheduler resolves via ``importlib``), so the lifespan's release
    keeper and any leader-gated scheduler tick become no-ops instead of writing
    to the shared test SQLite. Crucially it leaves ``Settings`` untouched, so
    unit tests still observe the real production default
    (``leader_election_enabled is True``). Tests that patch the leader election
    themselves (e.g. test_graceful_shutdown, the scheduler unit tests, and
    test_multi_replica) override this per-test and keep working.
    """
    import app.core.scheduling.leader_election as leader_election_module
    import app.main as main_module

    election = _NoopLeaderElection()
    monkeypatch.setattr(leader_election_module, "get_leader_election", lambda: election)
    monkeypatch.setattr(main_module, "get_leader_election", lambda: election)


@pytest.fixture(autouse=True)
def _forbid_blocking_test_client_on_running_loop(monkeypatch):
    """Fail fast when starlette's blocking ``TestClient`` is entered on the running test loop.

    ``TestClient.__enter__`` blocks the calling thread until the app lifespan
    has started on the client's portal thread. Called from an ``async def``
    test, the blocked thread is the shared session loop — the loop that owns
    the ``async_client`` lifespan's background SQLite writers. A write
    transaction one of them has in flight (INSERT executed, COMMIT not yet
    dispatched) can no longer release the single writer slot, so the portal
    lifespan's startup stamps wait out the 30 s ``busy_timeout`` and fail with
    ``database is locked`` (issue #1949). That deadlock is nondeterministic
    and looked like a lock-contention bug in production code; turn it into an
    immediate, explanatory failure instead. Async tests use
    ``tests.integration.off_loop_test_client.off_loop_test_client``; sync
    tests (no running loop on the calling thread) are unaffected.
    """
    from starlette.testclient import TestClient

    original_enter = TestClient.__enter__

    def _guarded_enter(self):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return original_enter(self)
        raise RuntimeError(
            "TestClient.__enter__ called on a thread with a running event loop: this blocks the loop "
            "that owns the app lifespan's SQLite writers and deadlocks the portal lifespan's startup "
            "writes until busy_timeout (issue #1949). In async tests use "
            "`async with off_loop_test_client(app) as client:` from tests.integration.off_loop_test_client."
        )

    monkeypatch.setattr(TestClient, "__enter__", _guarded_enter)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_engine():
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def db_setup(_reset_db_state):
    del _reset_db_state
    return True


@pytest_asyncio.fixture
async def async_client(app_instance):
    async def _drain_proxy_persistence(response) -> None:
        # Request-log writes and API-key settlements are detached from the
        # response path in production; tests assert on their effects right
        # after a response, so flush them per request to keep the historical
        # synchronous semantics inside the suite. The detach contract itself
        # is pinned by dedicated tests that bypass this hook.
        del response
        service = getattr(app_instance.state, "proxy_service", None)
        if service is not None and hasattr(service, "drain_persistence_tasks"):
            await service.drain_persistence_tasks(timeout_seconds=5)

    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(
            transport=transport,
            base_url="http://testserver",
            event_hooks={"response": [_drain_proxy_persistence]},
        ) as client:
            yield client
        # Reclaim bridge retries before the lifespan's own shutdown drain. A
        # retry that has already outlived the response hook would otherwise
        # consume the shutdown budget and keep the shared SQLite file active
        # while the next app fixture starts.
        await _reap_leaked_http_bridge_recovery_settlement_tasks(app_instance)


@pytest.fixture(autouse=True)
def _disable_default_refresh_claims():
    """Disable the process-default cross-replica refresh-claim coordinator.

    The default coordinator writes claim rows through the real database on
    every token refresh; unit tests exercise AuthManager against stub repos
    without a migrated schema. Tests covering claim semantics install a real
    ``RefreshClaimCoordinator`` explicitly (constructor injection or
    ``set_refresh_claim_coordinator``).
    """
    from app.modules.accounts import refresh_claims

    refresh_claims.set_refresh_claim_coordinator(None)
    yield
    refresh_claims.reset_refresh_claim_coordinator()


@pytest.fixture(autouse=True)
def _scope_plan_downgrade_observation_store(request):
    """Keep the cross-replica downgrade-observation store test-local.

    The process default persists observations through the real database so the
    paid -> free confirmation sequence stays coherent across replicas. Pure unit
    tests drive ``UsageUpdater`` against stub repositories with no migrated
    schema, so they get an isolated in-memory store that preserves
    single-process semantics. Integration tests keep the database-backed default
    (that is the behavior under test), and simply get a clean table per test.
    """
    from app.modules.usage import plan_downgrade_observations as observations_module

    is_unit_test = "/tests/unit/" in request.path.as_posix() if hasattr(request, "path") else False
    if is_unit_test:
        observations_module.set_plan_downgrade_observation_store(
            observations_module.InMemoryPlanDowngradeObservationStore()
        )
    yield
    observations_module.reset_plan_downgrade_observation_store()


@pytest.fixture(autouse=True)
def temp_key_file(monkeypatch):
    key_path = TEST_DB_DIR / f"encryption-{uuid4().hex}.key"
    monkeypatch.setenv("CODEX_LB_ENCRYPTION_KEY_FILE", str(key_path))
    from app.core.config.settings import get_settings

    get_settings.cache_clear()
    return key_path


@pytest.fixture(autouse=True)
def _reset_model_registry():
    from app.core.openai.model_registry import get_model_registry

    registry = get_model_registry()
    registry._snapshot = None
    registry._metadata_models = None
    registry._applied_content_hash = None
    yield
    registry._snapshot = None
    registry._metadata_models = None
    registry._applied_content_hash = None


@pytest.fixture(autouse=True)
def _reset_codex_version_cache():
    from app.core.clients.codex_version import get_codex_version_cache

    cache = get_codex_version_cache()
    cache._cached_version = None
    cache._cached_at = 0.0
    cache._retry_at = 0.0
    cache._cache_path = None
    yield
    cache._cached_version = None
    cache._cached_at = 0.0
    cache._retry_at = 0.0
    cache._cache_path = None


def _reset_global_state() -> None:
    """Reset global singletons that leak between tests."""
    try:
        from app.core.auth.api_key_cache import get_api_key_cache

        get_api_key_cache().clear()
    except Exception:
        pass
    try:
        from app.core.middleware.firewall_cache import get_firewall_ip_cache as get_firewall_cache

        get_firewall_cache().invalidate_all()
    except Exception:
        pass
    try:
        from app.modules.proxy.account_cache import clear_all_account_routing_unavailable, get_account_selection_cache

        get_account_selection_cache().invalidate()
        clear_all_account_routing_unavailable()
    except Exception:
        pass
    try:
        from app.core.cache.invalidation import set_cache_invalidation_poller

        set_cache_invalidation_poller(None)
    except Exception:
        pass
    try:
        from app.core.config.settings_cache import get_settings_cache

        settings_cache = get_settings_cache()
        settings_cache._cached_settings = None
        settings_cache._cached_at = 0.0
        # ``cached_row()`` deliberately survives an invalidation (a dashboard
        # value must not revert to the environment between a mutation and the
        # next load), so the fallback slot needs an explicit reset here or a
        # dashboard row leaks from one test into the next.
        settings_cache._last_loaded_settings = None
    except Exception:
        pass
    try:
        from app.core.auth.dashboard_users_cache import get_dashboard_users_cache

        get_dashboard_users_cache().clear()
    except Exception:
        pass
    try:
        from app.core.auth.providers.registry import get_auth_provider_registry
        from app.modules.dashboard_users.identity_resolver import get_identity_resolution_cache

        get_auth_provider_registry().clear()
        get_identity_resolution_cache().clear()
    except Exception:
        pass
    try:
        from app.core.upstream_proxy.cache import get_upstream_route_cache

        get_upstream_route_cache().clear()
    except Exception:
        pass
    try:
        from app.core.config.context_window_overrides import get_model_context_window_overrides_cache

        get_model_context_window_overrides_cache().clear()
    except Exception:
        pass
    try:
        # Pending workspace-less plan-downgrade confirmations live in a
        # process-global fallback store when persistence is disabled, so a test
        # that leaves one behind would otherwise give the next test a head start
        # toward a downgrade (issue #1456).
        from app.modules.usage.updater import _FALLBACK_PLAN_DOWNGRADE_OBSERVATIONS

        _FALLBACK_PLAN_DOWNGRADE_OBSERVATIONS.clear_all()
    except Exception:
        pass
    try:
        # Pending last-used touches would otherwise leak a previous test's key
        # ids into the next test's flush (harmless guarded UPDATEs, but noisy).
        from app.modules.api_keys.last_used_coalescer import get_api_key_last_used_coalescer

        get_api_key_last_used_coalescer().clear()
    except Exception:
        pass
    try:
        # Thread anchors are process-global by design (one live thread keeps
        # one derived prompt_cache_key), so a body reused by the next test
        # would otherwise resolve to the previous test's key and account.
        from app.modules.proxy.thread_anchors import reset_thread_anchor_index

        reset_thread_anchor_index()
    except Exception:
        pass
    try:
        from app.core.resilience.degradation import set_normal

        set_normal()
    except Exception:
        pass
    try:
        from app.core.shutdown import set_bridge_drain_active

        set_bridge_drain_active(False)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_hot_path_caches():
    """Reset T20 hot-path caches between tests to prevent state leakage."""
    _reset_global_state()
    yield
    _reset_global_state()


@pytest.fixture(autouse=True)
def _reset_shutdown_task_admission():
    """Keep the process-global shutdown admission barrier test-local."""
    from app.core import shutdown as shutdown_state

    shutdown_state.reset()
    yield
    shutdown_state.reset()


_session_loop: asyncio.AbstractEventLoop | None = None

# Both task names the live-usage ingestor owns (consumer and throttled
# trailing cache invalidation); the fence below reclaims them by name when the
# singleton no longer tracks them.
_LIVE_INGEST_TASK_NAMES = ("live-usage-ingestor", "live-usage-trailing-invalidation")


def _pending_live_ingest_tasks(loop: asyncio.AbstractEventLoop) -> list[asyncio.Task[Any]]:
    return [task for task in asyncio.all_tasks(loop) if not task.done() and task.get_name() in _LIVE_INGEST_TASK_NAMES]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _capture_session_loop():
    """Expose the shared session loop to sync fixture teardowns.

    The live-usage ingestor fence below must run coroutine cleanup from a
    synchronous teardown (see its docstring for why it cannot be an async
    fixture), and pytest-asyncio has no public API to reach the session loop
    from sync code.
    """
    global _session_loop
    _session_loop = asyncio.get_running_loop()
    yield
    _session_loop = None


async def _reap_leaked_live_usage_ingestor() -> None:
    """Stop and reset the live-usage ingestor singleton.

    Mirrors ``stop_live_usage_ingestor()`` but never re-raises: every awaited
    task ends up done, and ``_consume_dead_live_ingest_task_failures`` then
    retrieves and reports its exception exactly once. Also sweeps by name for
    ingestor-owned tasks (consumer and trailing invalidation) the stop path no
    longer tracks — a stop that was itself cancelled between clearing the
    global and awaiting the tasks.

    Only tasks bound to the loop this coroutine runs on are cancelled and
    awaited. A leaked singleton can hold tasks that belong to a different
    loop entirely — integration tests run ``TestClient`` portals whose loop
    is a private per-portal loop that is already closed by teardown time.
    Cancelling such a task raises ``RuntimeError('Event loop is closed')``
    from ``call_soon`` and awaiting it raises the cross-loop RuntimeError;
    neither can ever reap it. Those tasks are inert (a closed loop never
    steps again), so they are enrolled for exception accounting and left
    alone.
    """
    from app.core.usage.live_hub import register_live_usage_publisher
    from app.modules.usage import live_ingest

    ingestors: list[live_ingest.LiveUsageIngestor] = []
    if live_ingest._ingestor is not None:
        ingestors.append(live_ingest._ingestor)
    live_ingest._ingestor = None
    # Displaced (nested-over) registrations hold live tasks too, and a stale
    # stack entry must never be restored into a later test.
    ingestors.extend(live_ingest._displaced_ingestors)
    live_ingest._displaced_ingestors.clear()
    register_live_usage_publisher(None)
    leaked: list[asyncio.Task[None]] = []
    for ingestor in ingestors:
        for task in (ingestor._consumer, ingestor._trailing_invalidation):
            if task is not None and task not in leaked:
                leaked.append(task)
        ingestor._consumer = None
        ingestor._trailing_invalidation = None
    loop = asyncio.get_running_loop()
    for task in _pending_live_ingest_tasks(loop):
        if task not in leaked:
            leaked.append(task)
    reapable: list[asyncio.Task[None]] = []
    for task in leaked:
        live_ingest._owned_tasks.add(task)
        if task.get_loop() is loop:
            reapable.append(task)
    for task in reapable:
        task.cancel()
    for task in reapable:
        try:
            await task
        except (Exception, asyncio.CancelledError):
            # Settled and reported by _drain_live_ingest_task_failures.
            continue


def _drain_live_ingest_task_failures() -> list[str]:
    """Collect failures from dead ingestor-owned tasks, loop-free.

    ``asyncio.all_tasks`` only returns unfinished tasks, so a leaked task that
    already died with an exception is invisible to the pending sweep; its
    unretrieved exception would otherwise fire the loop exception handler when
    the task object is garbage-collected inside a LATER test (test_proxy_utils'
    startup-probe assertions capture exactly that). live_ingest's done
    callback normally settles each task the moment it completes (retrieving
    the exception into the strong ``_owned_task_failures`` handoff); the sweep
    over the weak registry here additionally settles tasks whose callback is
    still queued because the task finished in the loop's final iteration.
    Settlement is gated by live_ingest's settled-task registry, so each task
    is reported exactly once even when both paths observe it.
    """
    from app.modules.usage import live_ingest

    for task in list(live_ingest._owned_tasks):
        if task.done():
            live_ingest._record_owned_task_result(task)
    failures = [f"{name!r} died with {exc_repr}" for name, exc_repr in live_ingest._owned_task_failures]
    live_ingest._owned_task_failures.clear()
    return failures


@pytest.fixture(autouse=True)
def _stop_leaked_live_usage_ingestor():
    """Fence the module-global live-usage ingestor per test (issue #1755).

    The suite runs on a session-scoped asyncio loop, so a task leaked by one
    test survives into every later test. Any test that enters the real app
    lifespan starts the live-usage ingestor singleton
    (``app.modules.usage.live_ingest._ingestor``) whose ``live-usage-ingestor``
    consumer task lands on that shared loop; if the lifespan is cancelled
    before its shutdown path reaches ``stop_live_usage_ingestor()`` (e.g. a
    ``wait_for``-bounded assertion times out mid-drain), the consumer outlives
    the test. The zombie then poisons unrelated tests: it eats into the otel
    lifespan test's drain budget and surfaces as an unobserved-task exception
    inside test_proxy_utils' startup-probe loop-exception assertions — the
    exact failing pairing from #1755. Stop and reset the singleton after every
    test so no ingestor task ever crosses a test boundary.

    Deliberately a sync fixture that only enters the event loop when a leak is
    actually present: an async fixture's teardown would spin the shared loop
    after EVERY test, and the loop's clock calls ``time.monotonic()`` — which
    several tests monkeypatch globally with finite or call-count-sensitive
    fakes that are still active while function-scoped teardowns run (e.g.
    test_conversation_archive's exhausting iterator). Leak detection itself is
    loop-passive: reading the module globals, enumerating
    ``asyncio.all_tasks(loop)`` on the idle session loop, and retrieving
    exceptions from already-dead owned tasks never runs the loop.
    """
    yield
    from app.core.usage import live_hub
    from app.modules.usage import live_ingest

    loop = _session_loop
    loop_usable = loop is not None and not loop.is_closed() and not loop.is_running()
    needs_reap = (
        live_ingest._ingestor is not None
        or bool(live_ingest._displaced_ingestors)
        or live_hub._publisher is not None
        or (loop_usable and loop is not None and _pending_live_ingest_tasks(loop))
    )
    if needs_reap and loop_usable and loop is not None:
        loop.run_until_complete(_reap_leaked_live_usage_ingestor())
    failures = _drain_live_ingest_task_failures()
    if failures:
        pytest.fail(
            "test leaked a live-usage ingestor whose task(s) already failed: " + "; ".join(failures),
            pytrace=False,
        )


def _pending_audit_log_tasks() -> list[asyncio.Task[None]]:
    from app.core.audit import service as audit_service

    return [task for task in audit_service._AUDIT_LOG_TASKS if not task.done()]


async def _reap_leaked_audit_log_tasks() -> None:
    """Cancel audit-log tasks a test left behind and drop them from the registry.

    Only tasks bound to the loop this coroutine runs on are cancelled and
    awaited; a task that belongs to another (already closed) loop cannot be
    reaped from here and is inert, so it is only removed from the registry.
    Awaiting a cancelled task raises ``CancelledError`` in this coroutine
    without cancelling it — the exception is the task's outcome, not ours.
    """
    from app.core.audit import service as audit_service

    loop = asyncio.get_running_loop()
    leaked = _pending_audit_log_tasks()
    reapable = [task for task in leaked if task.get_loop() is loop]
    for task in reapable:
        task.cancel()
    for task in reapable:
        try:
            await task
        except (Exception, asyncio.CancelledError):
            continue
    for task in leaked:
        audit_service._AUDIT_LOG_TASKS.discard(task)


@pytest.fixture(autouse=True)
def _reap_leaked_audit_log_tasks_fence():
    """Fence the process-global audit-log task registry per test (issue #2209).

    ``AuditService.log_async`` is fire-and-forget: it schedules the database
    write as a task on the running loop and tracks it only in the module-global
    ``_AUDIT_LOG_TASKS`` set. A test that drives a login or mutation path and
    returns before that write is stepped (or without a provisioned schema for
    it to ever complete against) leaves the task pending on the shared session
    loop, where it survives into every later test. test_otel's lifespan drain
    test asserts the registry is empty before it starts and then fails on a
    leak it did not cause. Cancel and settle any leaked task after every test
    so none crosses a test boundary.

    Same shape as ``_stop_leaked_live_usage_ingestor`` above: a sync fixture
    that only enters the event loop when a leak is actually present, because
    spinning the loop after every test would call the loop clock while some
    tests still hold finite ``time.monotonic`` fakes during teardown.
    """
    yield
    if not _pending_audit_log_tasks():
        return
    loop = _session_loop
    if loop is not None and not loop.is_closed() and not loop.is_running():
        loop.run_until_complete(_reap_leaked_audit_log_tasks())
