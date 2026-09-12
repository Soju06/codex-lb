"""Opt-in test against an EMPTY dedicated PostgreSQL database, never the service DB."""

import json
import os
from datetime import datetime

import pytest
import sqlalchemy as sa

from scripts.local_postgres_import import migrate


@pytest.fixture
def databases(tmp_path):
    url = os.environ.get("LOCAL_POSTGRES_TEST_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL test database required")
    target = sa.create_engine(url)
    assert not sa.inspect(target).get_table_names(), "Test database must be empty"
    source_path = tmp_path / "source.db"
    source = sa.create_engine("sqlite:///" + str(source_path))
    metadata = sa.MetaData()
    revision = sa.Table("alembic_version", metadata, sa.Column("version_num", sa.String, primary_key=True))
    parent = sa.Table(
        "parents",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("secret", sa.LargeBinary),
        sa.Column("enabled", sa.Boolean),
        sa.Column("at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.Text),
    )
    child = sa.Table(
        "children",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("parent_id", sa.ForeignKey("parents.id")),
    )
    for engine in (source, target):
        metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(revision.insert().values(version_num="test"))
    with source.begin() as connection:
        connection.execute(
            parent.insert().values(
                id=19, secret=b"\x00\xffencrypted", enabled=True, at=datetime(2026, 9, 12, 1, 2, 3, 456789), note="中文"
            )
        )
        connection.execute(child.insert().values(id=3, parent_id=19))
    yield source_path, url, source, target, parent, child
    metadata.drop_all(target)
    source.dispose()
    target.dispose()


def test_roundtrip_and_generated_sequence(databases):
    path, url, _, target, parent, _ = databases
    before = path.read_bytes()
    report = migrate(path, url)
    assert report["parents"]["rows"] == report["children"]["rows"] == 1
    assert path.read_bytes() == before
    with target.begin() as connection:
        assert connection.execute(parent.insert().returning(parent.c.id)).scalar() == 20
    with pytest.raises(ValueError, match="not empty"):
        migrate(path, url)


def test_late_insert_failure_rolls_back_all_rows(databases):
    path, url, _, target, parent, child = databases
    with target.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE children ADD CONSTRAINT bad_parent CHECK (parent_id < 10)")
    with pytest.raises(sa.exc.IntegrityError):
        migrate(path, url)
    with target.connect() as connection:
        assert connection.execute(sa.select(parent)).all() == []
        assert connection.execute(sa.select(child)).all() == []


def test_schema_mismatch_preserves_empty_target(databases):
    path, url, _, target, parent, _ = databases
    with target.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE parents ADD COLUMN unexpected TEXT")
    with pytest.raises(ValueError, match="Column sets"):
        migrate(path, url)
    with target.connect() as connection:
        assert connection.execute(sa.select(parent)).all() == []


def test_postgres_gate_accepts_second_backend_and_rejects_wrong_revision(databases, tmp_path):
    from scripts.local_database_gate import check_plan

    _, url, _, target, _, _ = databases
    release = tmp_path / "release"
    versions = release / "app/db/alembic/versions"
    versions.mkdir(parents=True)
    (versions / "test.py").write_text('revision = "test"\ndown_revision = None\n')
    (release / "app/db/models.py").write_text("# unchanged schema\n")
    connection_file = tmp_path / "connection.json"
    connection_file.write_text(json.dumps({"url": url}))
    environments = {}
    for port in (2461, 2462):
        path = tmp_path / f"{port}.json"
        path.write_text(
            json.dumps(
                {
                    "CODEX_LB_DATABASE_URL": url.replace("postgresql+psycopg:", "postgresql+asyncpg:"),
                    "CODEX_LB_DATABASE_MIGRATE_ON_STARTUP": "false",
                    "CODEX_LB_ENCRYPTION_KEY_FILE": "/shared/key",
                    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID": str(port),
                }
            )
        )
        environments[str(port)] = str(path)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "driver": "postgresql",
                "candidates": {str(p): str(release) for p in (2461, 2462)},
                "active_release": str(release),
                "connection_file": str(connection_file),
                "backend_environments": environments,
                "encryption_key_file": "/shared/key",
            }
        )
    )
    assert check_plan(plan, 2462)["revision"] == "test"
    with pytest.raises(KeyError):
        check_plan(plan, 2463)
    with target.begin() as connection:
        connection.exec_driver_sql("UPDATE alembic_version SET version_num = 'wrong'")
    with pytest.raises(ValueError, match="revision mismatch"):
        check_plan(plan, 2462)
