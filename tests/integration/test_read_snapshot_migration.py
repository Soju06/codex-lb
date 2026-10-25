"""The additive derived-data migration preserves existing accounting and owners."""

from __future__ import annotations

from typing import cast

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Table,
    UniqueConstraint,
    create_engine,
    inspect,
    text,
)
from sqlalchemy.schema import DefaultClause, DropIndex

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url
from app.db.models import ReadSnapshot
from tests.integration.test_affinity_identity_migration import migration_url  # noqa: F401

pytestmark = pytest.mark.integration

_PARENT = "20260918_000000_merge_scim_and_overflow_heads"
_REVISION = "20261019_020000_add_read_snapshots"


def test_read_snapshots_upgrade_downgrade_preserves_accounting(migration_url):  # noqa: F811 - imported pytest fixture
    config = _build_alembic_config(migration_url)
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [_REVISION]
    assert script.get_revision(_REVISION).down_revision == _PARENT
    run_upgrade(migration_url, _PARENT, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO request_logs (request_id, model, status, requested_at, cost_usd) "
                    "VALUES ('snapshot-migration-control', 'synthetic', 'success', "
                    "'2026-10-19 00:00:00', 0.1234)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at) "
                    "VALUES (1, 'existing-metadata-owner', '2026-10-19 00:00:00', "
                    "'2026-10-19 01:00:00')"
                )
            )
            original = connection.execute(text("SELECT * FROM request_logs")).mappings().all()
        run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
        assert not check_schema_drift(migration_url)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM read_snapshots")).scalar_one() == 0
            # A real bundled dashboard can exceed 64 KiB.
            payload = '{"value":"' + "x" * 80_000 + '"}'
            connection.execute(
                text(
                    "INSERT INTO read_snapshots "
                    "(snapshot_key, kind, parameters_json, payload_json, refresh_at, requested_at) "
                    "VALUES (:key, 'dashboard', '{}', :payload, "
                    "'2026-10-19 00:00:00', '2026-10-19 00:00:00')"
                ),
                {"key": "a" * 64, "payload": payload},
            )
        run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT payload_json FROM read_snapshots")).scalar_one() == payload
            assert connection.execute(text("SELECT * FROM request_logs")).mappings().all() == original
            assert (
                connection.execute(text("SELECT leader_id FROM scheduler_leader WHERE id=1")).scalar_one()
                == "existing-metadata-owner"
            )
        command.downgrade(config, _PARENT)
        with engine.connect() as connection:
            assert "read_snapshots" not in inspect(connection).get_table_names()
            assert connection.execute(text("SELECT * FROM request_logs")).mappings().all() == original
        run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
        assert not check_schema_drift(migration_url)
    finally:
        engine.dispose()


@pytest.mark.parametrize("index_created", [False, True])
def test_read_snapshot_partial_upgrade_retries_preserve_derived_rows(migration_url, index_created):  # noqa: F811
    run_upgrade(migration_url, _PARENT, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        # Reproduce the interrupted-DDL boundary: table (optionally its
        # index) exists, but Alembic still records the parent revision.
        with engine.begin() as connection:
            cast(Table, ReadSnapshot.__table__).create(connection)
            if not index_created:
                connection.execute(DropIndex(next(iter(cast(Table, ReadSnapshot.__table__).indexes))))
            connection.execute(
                text(
                    "INSERT INTO read_snapshots "
                    "(snapshot_key,kind,parameters_json,payload_json,refresh_at,requested_at) "
                    "VALUES (:key,'dashboard','{}',:payload,'2026-10-19 00:00:00','2026-10-19 00:00:00')"
                ),
                {"key": "b" * 64, "payload": '{"lastGood":true}'},
            )
        run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
        assert not check_schema_drift(migration_url)
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT payload_json FROM read_snapshots")).scalar_one() == '{"lastGood":true}'
            )
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _REVISION
    finally:
        engine.dispose()


def test_read_snapshot_partial_downgrade_finishes_after_table_already_dropped(migration_url):  # noqa: F811
    run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO scheduler_leader (id,leader_id,acquired_at,expires_at) "
                    "VALUES (1,'preserved-owner','2026-10-19 00:00:00','2026-10-19 01:00:00')"
                )
            )
            cast(Table, ReadSnapshot.__table__).drop(connection)
        command.downgrade(_build_alembic_config(migration_url), _PARENT)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PARENT
            assert (
                connection.execute(text("SELECT leader_id FROM scheduler_leader WHERE id=1")).scalar_one()
                == "preserved-owner"
            )
        run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
        assert not check_schema_drift(migration_url)
    finally:
        engine.dispose()


def test_read_snapshot_retry_refuses_existing_incompatible_column_type(migration_url):  # noqa: F811
    run_upgrade(migration_url, _PARENT, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        table = cast(Table, ReadSnapshot.__table__).to_metadata(MetaData())
        table.c.payload_json.type = Integer()
        with engine.begin() as connection:
            table.create(connection)
        with pytest.raises(RuntimeError, match="Unexpected existing read_snapshots layout"):
            run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == _PARENT
            columns = {column["name"]: column for column in inspect(connection).get_columns("read_snapshots")}
            assert isinstance(columns["payload_json"]["type"], Integer)
    finally:
        engine.dispose()


@pytest.mark.parametrize("direction", ["upgrade", "downgrade"])
@pytest.mark.parametrize(
    "layout", ["default", "unique", "foreign-key", "check", "extra-index", "column-type", "generated"]
)
def test_read_snapshot_migration_refuses_alien_layout_without_deleting_rows(migration_url, direction, layout):  # noqa: F811
    revision = _PARENT if direction == "upgrade" else _REVISION
    run_upgrade(migration_url, revision, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        table = cast(Table, ReadSnapshot.__table__).to_metadata(MetaData())
        if layout == "default":
            table.c.generation.server_default = DefaultClause(text("999"))
        elif layout == "unique":
            table.append_constraint(UniqueConstraint("kind", name="alien_unique_kind"))
        elif layout == "foreign-key":
            table.append_constraint(ForeignKeyConstraint(["last_error"], ["read_snapshots.snapshot_key"]))
        elif layout == "check":
            table.append_constraint(CheckConstraint("generation >= 0", name="alien_generation_check"))
        elif layout == "extra-index":
            Index("alien_kind_index", table.c.kind)
        elif layout == "generated":
            table.c.payload_json.computed = Computed(
                "'alien generated payload'", persisted=engine.dialect.name == "postgresql"
            )
            table.c.payload_json.server_default = table.c.payload_json.computed
            table.c.payload_json.server_onupdate = table.c.payload_json.computed
        else:
            table.c.payload_json.type = Integer()
        with engine.begin() as connection:
            if direction == "downgrade":
                cast(Table, ReadSnapshot.__table__).drop(connection)
            table.create(connection)
            connection.execute(
                text(
                    "INSERT INTO read_snapshots (snapshot_key,kind,parameters_json,refresh_at,requested_at) "
                    "VALUES (:key,'dashboard','{}','2026-10-19 00:00:00','2026-10-19 00:00:00')"
                ),
                {"key": "c" * 64},
            )
        with pytest.raises(RuntimeError, match="Unexpected existing read_snapshots"):
            if direction == "upgrade":
                run_upgrade(migration_url, _REVISION, bootstrap_legacy=False)
            else:
                command.downgrade(_build_alembic_config(migration_url), _PARENT)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == revision
            assert connection.execute(text("SELECT snapshot_key FROM read_snapshots")).scalar_one() == "c" * 64
    finally:
        engine.dispose()
