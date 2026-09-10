from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration

_GROUP_HEAD = "20260910_000000_add_api_key_usage_group"
_UPSTREAM_HEAD = "20260909_060000_add_report_rollup"
_MERGED_HEAD = "20260910_010000_merge_beta6_and_key_groups"


@pytest.mark.parametrize("starting_revision", [None, _GROUP_HEAD, _UPSTREAM_HEAD], ids=["fresh", "fork", "upstream"])
def test_beta6_upgrade_converges_and_preserves_key_data(tmp_path: Path, starting_revision: str | None) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'beta6.db'}"
    _verify_beta6_upgrade(url, starting_revision)


def _verify_beta6_upgrade(url: str, starting_revision: str | None) -> None:
    engine = create_engine(url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg"))
    try:
        if starting_revision is not None:
            run_upgrade(url, starting_revision, bootstrap_legacy=False)
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active) "
                        "VALUES ('existing', 'Existing', 'hash', 'prefix', true)"
                    )
                )
                conn.execute(
                    text(
                        "INSERT INTO api_key_limits "
                        "(api_key_id, limit_type, limit_window, max_value, current_value, reset_at) "
                        "VALUES ('existing', 'total_tokens', 'daily', 1000, 12, '2026-09-10 17:00:00')"
                    )
                )
                if starting_revision == _GROUP_HEAD:
                    conn.execute(text("UPDATE api_keys SET usage_group = 'team-a' WHERE id = 'existing'"))

        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        assert inspect(engine).has_table("request_report_hourly_rollups")
        assert "ix_api_keys_usage_group" in {index["name"] for index in inspect(engine).get_indexes("api_keys")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [_MERGED_HEAD]
            if starting_revision is not None:
                row = conn.execute(text("SELECT id, name, usage_group FROM api_keys")).one()
                assert tuple(row) == ("existing", "Existing", "team-a" if starting_revision == _GROUP_HEAD else None)
                assert tuple(conn.execute(text("SELECT max_value, current_value FROM api_key_limits")).one()) == (
                    1000,
                    12,
                )

        # Downgrade only the no-op merge, leaving both additive parent schemas.
        config = _build_alembic_config(url)
        command.downgrade(config, _UPSTREAM_HEAD)
        with engine.connect() as conn:
            assert set(conn.execute(text("SELECT version_num FROM alembic_version")).scalars()) == {
                _GROUP_HEAD,
                _UPSTREAM_HEAD,
            }
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        if starting_revision == _GROUP_HEAD:
            with engine.connect() as conn:
                assert (
                    conn.execute(text("SELECT usage_group FROM api_keys WHERE id = 'existing'")).scalar_one()
                    == "team-a"
                )
    finally:
        engine.dispose()


def test_beta6_merge_keeps_deployed_group_parent_and_one_head(tmp_path: Path) -> None:
    config = _build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.db'}")
    scripts = ScriptDirectory.from_config(config)
    assert scripts.get_heads() == [_MERGED_HEAD]
    group = scripts.get_revision(_GROUP_HEAD)
    assert group is not None
    assert group.down_revision == "20260830_000000_add_quota_warmup_claim_expiry"
