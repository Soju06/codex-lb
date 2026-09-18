from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

_WORKTREE = Path(__file__).resolve().parents[2]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

_MAIN_CHECKOUT = Path("/Volumes/StudioExt/repos/agent-lb")
_TEST_DIR = Path(tempfile.mkdtemp(prefix="agent-lb-openrouter-"))
_DB_PATH = _TEST_DIR / "store.db"

os.environ["AGENT_LB_DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB_PATH}"
os.environ["AGENT_LB_UPSTREAM_BASE_URL"] = "https://example.invalid/backend-api"
os.environ["AGENT_LB_USAGE_REFRESH_ENABLED"] = "false"
os.environ["AGENT_LB_MODEL_REGISTRY_ENABLED"] = "false"
os.environ["AGENT_LB_STICKY_SESSION_CLEANUP_ENABLED"] = "false"
os.environ["AGENT_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED"] = "false"
os.environ["AGENT_LB_QUOTA_PLANNER_SCHEDULER_ENABLED"] = "false"
os.environ["AGENT_LB_ENCRYPTION_KEY_FILE"] = str(_TEST_DIR / f"encryption-{uuid4().hex}.key")


def worktree_root() -> Path:
    return _WORKTREE


def main_checkout() -> Path:
    return _MAIN_CHECKOUT


@asynccontextmanager
async def app_client():
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    import app.main as main_module
    from app.core.config.settings import get_settings
    from app.db.models import Base
    from app.db.session import close_db, engine
    from app.main import create_app

    get_settings.cache_clear()
    await close_db()

    def _recreate(sync_conn) -> None:
        sync_conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        sync_conn.execute(text("DROP TABLE IF EXISTS schema_migrations"))
        Base.metadata.drop_all(sync_conn)
        Base.metadata.create_all(sync_conn)

    async with engine.begin() as conn:
        await conn.run_sync(_recreate)

    original_init_db = main_module.init_db

    async def _noop_init_db() -> None:
        return None

    main_module.init_db = _noop_init_db
    app = create_app()
    try:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://testserver") as client:
                yield client
    finally:
        main_module.init_db = original_init_db
        await close_db()
        await engine.dispose()


def run(coro):
    return asyncio.run(coro)
