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

from app.db.models import Base  # noqa: E402
from app.modules.proxy.response_context_cache import get_response_context_cache  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest_asyncio.fixture
async def app_instance():
    app = create_app()
    async with engine.begin() as conn:

        def _reset(sync_conn):
            sync_conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            sync_conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))
            Base.metadata.drop_all(sync_conn)
            Base.metadata.create_all(sync_conn)

        await conn.run_sync(_reset)
    return app


@pytest_asyncio.fixture(scope="session", autouse=True)
async def dispose_engine():
    yield
    await engine.dispose()


@pytest_asyncio.fixture
async def db_setup():
    async with engine.begin() as conn:

        def _reset(sync_conn):
            sync_conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
            sync_conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))
            Base.metadata.drop_all(sync_conn)
            Base.metadata.create_all(sync_conn)

        await conn.run_sync(_reset)
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
def _reset_response_context_cache():
    cache = get_response_context_cache()
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _reset_codex_version_cache():
    from app.core.clients.codex_version import get_codex_version_cache

    cache = get_codex_version_cache()
    cache._cached_version = None
    cache._cached_at = 0.0
    yield
    cache._cached_version = None
    cache._cached_at = 0.0
