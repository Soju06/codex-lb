"""Exercise the overflow/transport merge from each populated branch state."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration

_COMMON_PARENT = "20260830_000000_add_quota_warmup_claim_expiry"
_OVERFLOW = "20260908_000000_add_subscription_overflow"
_TRANSPORT = "20260908_000000_replace_upstream_stream_transport_default_sentinel"
_PARENTS = (_OVERFLOW, _TRANSPORT)
_MERGE = "20260908_020000_merge_overflow_transport_heads"


@dataclass
class _MigrationDatabase:
    url: str
    engine: Engine
    starting_revisions: tuple[str, ...]


def _seed_settings_and_retry(connection: Connection) -> None:
    connection.execute(
        text(
            "UPDATE dashboard_settings SET upstream_stream_transport = 'default', "
            "sticky_threads_enabled = 0 WHERE id = 1"
        )
    )
    seeded = dict(connection.execute(text("SELECT * FROM dashboard_settings WHERE id = 1")).mappings().one())
    columns = ", ".join(seeded)
    parameters = ", ".join(f":{column}" for column in seeded)
    for row_id, transport in enumerate(("http", "websocket", "auto"), start=2):
        connection.execute(
            text(f"INSERT INTO dashboard_settings ({columns}) VALUES ({parameters})"),
            {**seeded, "id": row_id, "upstream_stream_transport": transport},
        )
    connection.execute(
        text(
            """
            INSERT INTO http_bridge_retry_circuits (
                session_key_kind, session_key_hash, api_key_scope, consecutive_failures,
                cooldown_until_epoch, last_detail, updated_at_epoch, admission_generation
            ) VALUES ('session_header', 'retained-retry', '__anonymous__', 2,
                      1300.0, 'stream_incomplete', 1200.0, 7)
            """
        )
    )


def _seed_overflow(connection: Connection) -> None:
    connection.execute(
        text(
            "UPDATE dashboard_settings SET subscription_overflow_source_id = 'retained-source', "
            "subscription_overflow_drain_until = '2026-09-09 12:00:00' WHERE id = 1"
        )
    )
    connection.execute(
        text(
            """
            INSERT INTO model_source_pins (
                pin_key, kind, source_id, api_key_id, created_at, last_seen_at, expires_at, purge_at
            ) VALUES (:pin_key, :kind, 'retained-source', :api_key_id,
                      '2026-09-08 10:00:00', '2026-09-08 10:30:00',
                      '2026-09-09 10:00:00', '2026-09-10 10:00:00')
            """
        ),
        [
            {"pin_key": "retained-thread", "kind": "thread", "api_key_id": None},
            {"pin_key": "retained-anchor", "kind": "anchor", "api_key_id": "retained-api-key"},
        ],
    )


def _revisions(engine: Engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        return tuple(connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars())


def _state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        has_pins = inspector.has_table("model_source_pins")
        tables = ("dashboard_settings", "model_source_pins") if has_pins else ("dashboard_settings",)
        return {
            "settings": [
                dict(row) for row in connection.execute(text("SELECT * FROM dashboard_settings ORDER BY id")).mappings()
            ],
            "pins": (
                [
                    dict(row)
                    for row in connection.execute(text("SELECT * FROM model_source_pins ORDER BY pin_key")).mappings()
                ]
                if has_pins
                else None
            ),
            "retry": tuple(
                connection.execute(
                    text("SELECT * FROM http_bridge_retry_circuits WHERE session_key_hash = 'retained-retry'")
                ).one()
            ),
            "schema": {
                table: {
                    "columns": [
                        (column["name"], str(column["type"]), column["nullable"], column["default"])
                        for column in inspector.get_columns(table)
                    ],
                    "primary_key": inspector.get_pk_constraint(table),
                    "foreign_keys": inspector.get_foreign_keys(table),
                    "indexes": inspector.get_indexes(table),
                }
                for table in tables
            },
        }


@pytest.fixture(
    params=[(_OVERFLOW,), (_TRANSPORT,), _PARENTS],
    ids=["overflow-parent", "transport-parent", "both-parents"],
)
def branch_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_MigrationDatabase]:
    database_path = tmp_path / "merge-parents.sqlite"
    url = f"sqlite+aiosqlite:///{database_path}"
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        run_upgrade(url, _COMMON_PARENT, bootstrap_legacy=False)
        with engine.begin() as connection:
            _seed_settings_and_retry(connection)
        starting_revisions = request.param
        for revision in starting_revisions:
            run_upgrade(url, revision, bootstrap_legacy=False)
            if revision == _OVERFLOW:
                with engine.begin() as connection:
                    _seed_overflow(connection)
        assert _revisions(engine) == tuple(sorted(starting_revisions))
        yield _MigrationDatabase(url, engine, starting_revisions)
    finally:
        engine.dispose()


def test_overflow_transport_merge_is_the_only_head_with_both_original_parents(tmp_path: Path) -> None:
    config = _build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}")
    script = ScriptDirectory.from_config(config)
    # Later revisions build on the merge; the graph must still have one head
    # and the merge must be on its ancestry.
    heads = script.get_heads()
    assert len(heads) == 1
    assert _MERGE in {revision.revision for revision in script.iterate_revisions(heads[0], "base")}
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS
    for revision in _PARENTS:
        parent = script.get_revision(revision)
        assert parent is not None and parent.down_revision == _COMMON_PARENT


def test_populated_parent_upgrade_and_direct_downgrades_preserve_both_branches(
    branch_database: _MigrationDatabase,
) -> None:
    database = branch_database
    before = _state(database.engine)
    expected_settings = [dict(row) for row in before["settings"]]
    for row in expected_settings:
        if _TRANSPORT not in database.starting_revisions and row["upstream_stream_transport"] == "default":
            row["upstream_stream_transport"] = "auto"
        if _OVERFLOW not in database.starting_revisions:
            row["subscription_overflow_source_id"] = None
            row["subscription_overflow_drain_until"] = None

    result = run_upgrade(database.url, "head", bootstrap_legacy=False)
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert result.current_revision == head
    assert _revisions(database.engine) == (head,)
    merged = _state(database.engine)
    # Revisions after the merge add dashboard_settings columns (the resilience
    # toggles, the guest session counter, ...). Whether nullable or NOT NULL
    # with a server default, each is backfilled uniformly, so it carries no
    # per-row state: assert one value across rows and compare the rest
    # separately, so this test keeps covering the two original branches.
    merged_settings = [dict(row) for row in merged["settings"]]
    added_columns = set(merged_settings[0]) - set(expected_settings[0])
    for column in added_columns:
        backfilled = {row.pop(column) for row in merged_settings}
        assert len(backfilled) == 1, (column, backfilled)
    # Revisions after the merge also *retire* dashboard_settings columns (the
    # legacy credential trio, once `dashboard_users` became the only authority).
    # A column that no longer exists carries no per-row state either, and it is
    # not a fact about the two branches this test covers.
    for column in set(expected_settings[0]) - set(merged_settings[0]):
        for row in expected_settings:
            row.pop(column)
    assert merged_settings == expected_settings
    assert [row["upstream_stream_transport"] for row in merged["settings"]] == ["auto", "http", "websocket", "auto"]
    assert merged["pins"] == (before["pins"] if before["pins"] is not None else [])
    assert merged["retry"] == before["retry"]
    assert check_schema_drift(database.url) == ()

    # Populate the newly created overflow schema too, so every starting state
    # tests direct downgrade with retained settings and non-empty pins.
    if _OVERFLOW not in database.starting_revisions:
        with database.engine.begin() as connection:
            _seed_overflow(connection)
    populated = _state(database.engine)
    assert len(populated["pins"]) == 2
    assert populated["settings"][0]["subscription_overflow_source_id"] == "retained-source"

    # Step back to the merge first: revisions after it own their own schema
    # (and their own drift against the ORM), while the merge itself must stay a
    # no-op in both directions.
    command.downgrade(_build_alembic_config(database.url), _MERGE)
    assert _revisions(database.engine) == (_MERGE,)
    at_merge = _state(database.engine)
    merge_drift = check_schema_drift(database.url)

    for parent in _PARENTS:
        command.downgrade(_build_alembic_config(database.url), parent)
        # A direct downgrade to either immediate parent executes only the
        # no-op merge downgrade. Alembic records both unmerged parent heads;
        # it does not execute either parent's schema-removing downgrade.
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert _state(database.engine) == at_merge
        assert check_schema_drift(database.url) == merge_drift

        result = run_upgrade(database.url, "head", bootstrap_legacy=False)
        assert result.current_revision == head
        assert _revisions(database.engine) == (head,)
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == ()
