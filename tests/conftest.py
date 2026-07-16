from __future__ import annotations

import os
import tempfile
from pathlib import Path
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
os.environ["CODEX_LB_USAGE_REFRESH_ENABLED"] = "false"
os.environ["CODEX_LB_MODEL_REGISTRY_ENABLED"] = "false"
os.environ["CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED"] = "false"
os.environ["CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED"] = "false"
os.environ["CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED"] = "false"
# The app-level automations scheduler ticks on the real clock; with leader
# election enabled its startup tick runs as a background task and can land
# inside a test that stages its own due-now jobs, racing the test's
# claim_run. Tests drive automations via AutomationsService.run_due_jobs
# with explicit clocks or construct AutomationsScheduler directly.
os.environ["CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED"] = "false"
# NOTE: Leader election is intentionally NOT disabled via an env override here.
# It is default-enabled in production, and a global override would leak into
# every ``Settings()`` constructed anywhere in the suite — breaking the
# production-default assertion in test_settings_multi_replica.py. Instead the
# ambient app lifespan's leader election is replaced with a no-op by the autouse
# ``_disable_leader_election_startup`` fixture below (see its docstring).

from app.db.models import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import create_app  # noqa: E402


class _NoopScheduler:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


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

    async def release(self) -> None:
        return None


def _drop_test_migration_tables(sync_conn) -> None:
    sync_conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    sync_conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))


def _recreate_test_schema(sync_conn) -> None:
    _drop_test_migration_tables(sync_conn)
    Base.metadata.drop_all(sync_conn)
    Base.metadata.create_all(sync_conn)


def _reset_test_database(sync_conn) -> None:
    _recreate_test_schema(sync_conn)


@pytest_asyncio.fixture
async def _reset_db_state():
    from app.db.session import close_db

    await close_db()
    async with engine.begin() as conn:
        await conn.run_sync(_reset_test_database)
    return True


@pytest_asyncio.fixture
async def app_instance(_reset_db_state, monkeypatch):
    del _reset_db_state
    import app.main as main_module

    async def _noop_init_db() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", _noop_init_db)
    monkeypatch.setattr(main_module, "build_rate_limit_reset_credits_scheduler", lambda: _NoopScheduler())
    app = create_app()
    return app


@pytest.fixture(autouse=True)
def _disable_request_log_count_cache(monkeypatch):
    """Zero the request-log COUNT cache TTL so listing totals stay exact
    within a test. The TTL is a fixed constant in production (issue #1340
    phase 2); the cache-behavior test patches it back to a positive value."""
    import app.modules.request_logs.repository as logs_repository_module

    monkeypatch.setattr(logs_repository_module, "_COUNT_CACHE_TTL_SECONDS", 0.0)


@pytest.fixture(autouse=True)
def _disable_rate_limit_reset_credits_scheduler_startup(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "build_rate_limit_reset_credits_scheduler", lambda: _NoopScheduler())


@pytest.fixture(autouse=True)
def _disable_account_usage_rollup_scheduler_startup(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "build_account_usage_rollup_scheduler", lambda: _NoopScheduler())


@pytest.fixture(autouse=True)
def _disable_data_retention_scheduler_startup(monkeypatch):
    import app.main as main_module

    monkeypatch.setattr(main_module, "build_data_retention_scheduler", lambda: _NoopScheduler())


@pytest.fixture(autouse=True)
def _disable_leader_election_startup(monkeypatch):
    """Replace the ambient app-lifespan leader election with a no-op.

    Scoped exactly like the sibling ``_disable_*_scheduler_startup`` fixtures:
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
    yield
    cache._cached_version = None
    cache._cached_at = 0.0


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
