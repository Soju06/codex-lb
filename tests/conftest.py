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

from app.db.models import Base  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import create_app  # noqa: E402


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
    async with engine.begin() as conn:
        await conn.run_sync(_reset_test_database)
    return True


@pytest_asyncio.fixture
async def app_instance(_reset_db_state):
    del _reset_db_state
    app = create_app()
    return app


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
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


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
    yield
    registry._snapshot = None


@pytest.fixture(autouse=True)
def _reset_codex_version_cache():
    from app.core.clients.codex_version import get_codex_version_cache

    cache = get_codex_version_cache()
    cache._cached_version = None
    cache._cached_at = 0.0
    yield
    cache._cached_version = None
    cache._cached_at = 0.0


@pytest.fixture(autouse=True)
def _reset_hot_path_caches():
    """Reset T20 hot-path caches between tests to prevent state leakage."""
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
        from app.modules.proxy.account_cache import get_account_selection_cache

        get_account_selection_cache().invalidate()
    except Exception:
        pass
    try:
        from app.core.resilience.degradation import set_normal

        set_normal()
    except Exception:
        pass
    yield
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
        from app.modules.proxy.account_cache import get_account_selection_cache

        get_account_selection_cache().invalidate()
    except Exception:
        pass
    try:
        from app.core.resilience.degradation import set_normal

        set_normal()
    except Exception:
        pass
