"""Merge populated CPA and guest-generation branches without changing their data."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration

_COMMON = "20260910_010000_dashboard_spool_retention"
_CPA = "20260910_020000_merge_cpa_spool_retention"
_GUEST = "20260908_000000_add_guest_session_generation"
_PARENTS = (_CPA, _GUEST)
_MERGE = "20260910_030000_merge_cpa_guest_heads"
_RETENTION = "http_responses_session_bridge_operation_spool_retention_seconds"
_KEY = b"\x00retained-encrypted-key\xff"


@dataclass
class _BranchDatabase:
    url: str
    engine: Engine
    parents: tuple[str, ...]


def _revisions(engine: Engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        return tuple(connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars())


def _state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = ("model_sources", "model_source_models", "dashboard_settings")
        return {
            table: {
                "rows": [
                    dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()
                ],
                "columns": [
                    (column["name"], str(column["type"]), column["nullable"], column["default"])
                    for column in inspector.get_columns(table)
                ],
                "primary_key": inspector.get_pk_constraint(table),
                "foreign_keys": inspector.get_foreign_keys(table),
                "indexes": inspector.get_indexes(table),
            }
            for table in tables
        }


def _seed_branch(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        if revision == _CPA:
            connection.execute(
                text(
                    "UPDATE model_sources SET catalog_mode = 'cli_proxy_api', catalog_refresh_token = 'owned-refresh', "
                    "catalog_next_refresh_at = '2026-09-11 12:00:00' WHERE id = 'retained'"
                )
            )
        else:
            connection.execute(text("UPDATE dashboard_settings SET guest_session_generation = 7 WHERE id = 1"))


@pytest.fixture(params=[(_CPA,), (_GUEST,), _PARENTS], ids=["cpa-parent", "guest-parent", "both-parents"])
def branch_database(request: pytest.FixtureRequest, tmp_path: Path, db_setup) -> Iterator[_BranchDatabase]:
    configured_url = get_settings().database_url
    postgres = configured_url.startswith("postgresql+")
    url = configured_url if postgres else f"sqlite+aiosqlite:///{tmp_path / 'cpa-guest.sqlite'}"
    engine = create_engine(to_sync_database_url(url))
    try:
        if postgres:
            # db_setup owns the dedicated test database. Rebuild its historical
            # schema through Alembic instead of stamping the current ORM schema.
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        run_upgrade(url, _COMMON, bootstrap_legacy=False)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO model_sources (id, name, base_url, api_key_encrypted) "
                    "VALUES ('retained', 'CPA', 'http://localhost:8317/v1', :key)"
                ),
                {"key": _KEY},
            )
            connection.execute(
                text("INSERT INTO model_source_models (source_id, model) VALUES ('retained', 'kept-model')")
            )
        with engine.begin() as connection:
            connection.execute(text(f"UPDATE dashboard_settings SET {_RETENTION} = 86400.5 WHERE id = 1"))
        parents = request.param
        for revision in parents:
            run_upgrade(url, revision, bootstrap_legacy=False)
            _seed_branch(engine, revision)
        assert _revisions(engine) == tuple(sorted(parents))
        yield _BranchDatabase(url, engine, parents)
    finally:
        engine.dispose()


def test_cpa_guest_merge_is_on_single_head_graph_and_keeps_both_original_parents(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    heads = script.get_heads()
    assert len(heads) == 1
    assert _MERGE in {revision.revision for revision in script.iterate_revisions(heads[0], "base")}
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS
    cpa = script.get_revision(_CPA)
    guest = script.get_revision(_GUEST)
    assert cpa is not None and cpa.down_revision == ("20260910_000000_add_cpa_catalog_discovery", _COMMON)
    assert guest is not None and guest.down_revision == _COMMON


def test_populated_branches_merge_and_downgrade_without_data_loss(branch_database: _BranchDatabase) -> None:
    database = branch_database
    before = _state(database.engine)
    result = run_upgrade(database.url, _MERGE, bootstrap_legacy=False)
    assert result.current_revision == _MERGE
    assert _revisions(database.engine) == (_MERGE,)
    merged = _state(database.engine)
    source = merged["model_sources"]["rows"][0]
    assert source["id"] == "retained"
    assert source["api_key_encrypted"] == _KEY
    assert source["catalog_mode"] == ("cli_proxy_api" if _CPA in database.parents else "manual")
    assert source["catalog_refresh_token"] == ("owned-refresh" if _CPA in database.parents else None)
    if _CPA not in database.parents:
        assert source["catalog_next_refresh_at"] is None
    assert merged["dashboard_settings"]["rows"][0][_RETENTION] == 86400.5
    assert merged["dashboard_settings"]["rows"][0]["guest_session_generation"] == (
        7 if _GUEST in database.parents else 0
    )
    assert merged["model_source_models"] == before["model_source_models"]
    for table in ("model_sources", "dashboard_settings"):
        # Existing fields retain their exact values; the missing branch only
        # adds its own columns, whose defaults are checked above.
        for old, new in zip(before[table]["rows"], merged[table]["rows"], strict=True):
            assert {column: new[column] for column in old} == old
    historical_drift = check_schema_drift(database.url)
    _assert_historical_identity_drift(historical_drift)

    for parent in _PARENTS:
        if parent not in database.parents:
            _seed_branch(database.engine, parent)
    populated = _state(database.engine)
    for parent in _PARENTS:
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == historical_drift
        result = run_upgrade(database.url, _MERGE, bootstrap_legacy=False)
        assert result.current_revision == _MERGE
        assert _revisions(database.engine) == (_MERGE,)
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == historical_drift

    result = run_upgrade(database.url, "head", bootstrap_legacy=False)
    script = ScriptDirectory.from_config(_build_alembic_config(database.url))
    assert result.current_revision == script.get_heads()[0]
    assert _state(database.engine) == populated
    assert check_schema_drift(database.url) == ()


def _assert_historical_identity_drift(drift: tuple[str, ...], *, missing_guest: bool = False) -> None:
    expected_prefixes = [
        *(
            f"('add_table', Table('{table}',"
            for table in (
                "dashboard_roles",
                "dashboard_role_grants",
                "dashboard_users",
                "dashboard_identities",
                "dashboard_user_invites",
            )
        ),
        "('add_index', Index('idx_dashboard_identities_user_id',",
        "('add_index', Index('idx_audit_logs_actor_user_id',",
        "('add_index', Index('idx_audit_logs_target',",
        *(
            f"('add_column', None, 'audit_logs', Column('{column}',"
            for column in (
                "actor_user_id",
                "actor_username",
                "actor_role_slug",
                "auth_method",
                "target_type",
                "target_id",
                "severity",
            )
        ),
        *(
            f"('add_column', None, 'api_keys', Column('{column}',"
            for column in ("owner_user_id", "created_by_user_id", "deactivated_reason")
        ),
    ]
    if missing_guest:
        expected_prefixes.append(
            "('add_column', None, 'dashboard_settings', Column('guest_session_generation', Integer()"
        )
    remaining = list(drift)
    for prefix in expected_prefixes:
        matches = [entry for entry in remaining if entry.startswith(prefix)]
        assert len(matches) == 1, (prefix, drift)
        remaining.remove(matches[0])
    assert len(remaining) == 2
    for column in ("owner_user_id", "created_by_user_id"):
        matches = [
            entry
            for entry in remaining
            if entry.startswith("('add_fk',")
            and f"Column('{column}', NullType(), ForeignKey('dashboard_users.id')" in entry
        ]
        assert len(matches) == 1, (column, drift)
        remaining.remove(matches[0])
    assert remaining == []
