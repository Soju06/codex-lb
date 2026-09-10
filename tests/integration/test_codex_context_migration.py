from __future__ import annotations

import sqlite3

import pytest
from alembic.script import ScriptDirectory
from anyio import to_thread

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration
DEPLOYED_CONTEXT = "20260905_120000_add_codex_context_ownership"
UPSTREAM_HEAD = "20260910_000000_request_logs_missing_cost_index"
MERGED_HEAD = "20260910_120000_merge_codex_context_heads"


@pytest.mark.parametrize("starting_revision", [DEPLOYED_CONTEXT, UPSTREAM_HEAD])
async def test_deployed_context_and_fresh_upstream_upgrade_to_single_head(tmp_path, starting_revision):
    database = tmp_path / "context-upgrade.sqlite"
    url = f"sqlite+aiosqlite:///{database}"
    script = ScriptDirectory.from_config(_build_alembic_config(url))
    assert script.get_heads() == [MERGED_HEAD]
    assert script.get_revision(DEPLOYED_CONTEXT).down_revision == "20260830_000000_add_quota_warmup_claim_expiry"
    await to_thread.run_sync(lambda: run_upgrade(url, starting_revision, bootstrap_legacy=False))

    with sqlite3.connect(database) as db:
        if starting_revision == DEPLOYED_CONTEXT:
            columns = {row[1] for row in db.execute("PRAGMA table_info(dashboard_settings)")}
            assert "model_context_window_overrides" not in columns
            db.execute(
                "INSERT INTO codex_context_sessions VALUES ('00000000-0000-4000-8000-000000000011','key','owner')"
            )
            db.executemany(
                "INSERT INTO codex_context_participants VALUES ('00000000-0000-4000-8000-000000000011', ?)",
                [("owner",), ("rotated-account",)],
            )
            owners = db.execute("SELECT * FROM codex_context_sessions").fetchall()
            participants = db.execute("SELECT * FROM codex_context_participants ORDER BY account_id").fetchall()
        else:
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'codex_context_sessions'").fetchall()
            owners, participants = [], []

    result = await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
    assert result.current_revision == MERGED_HEAD
    assert not await to_thread.run_sync(lambda: check_schema_drift(url))
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT * FROM alembic_version").fetchall() == [(MERGED_HEAD,)]
        assert db.execute("SELECT * FROM codex_context_sessions").fetchall() == owners
        assert db.execute("SELECT * FROM codex_context_participants ORDER BY account_id").fetchall() == participants
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
