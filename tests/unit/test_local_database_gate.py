from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts.local_database_gate import check


def release(path: Path, revision: str = "20260912_000000_test") -> Path:
    versions = path / "app/db/alembic/versions"
    versions.mkdir(parents=True)
    (versions / f"{revision}.py").write_text(f'revision = "{revision}"\ndown_revision = None\n')
    (path / "app/db/models.py").write_text("# schema\n")
    return path


def database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version(version_num TEXT)")
        connection.execute("INSERT INTO alembic_version VALUES ('20260912_000000_test')")
    return path


def test_matching_schemas_are_checked_without_modifying_database(tmp_path):
    db = database(tmp_path / "store.db")
    before = db.read_bytes()
    assert check(db, release(tmp_path / "a"), release(tmp_path / "b"))["revision"] == "20260912_000000_test"
    assert db.read_bytes() == before


@pytest.mark.parametrize("difference", ["head", "migration", "orm"])
def test_schema_mismatch_rejected_even_if_revision_was_reused(tmp_path, difference):
    db = database(tmp_path / "store.db")
    before = db.read_bytes()
    a = release(tmp_path / "a")
    b = release(tmp_path / "b", "20260913_000000_test" if difference == "head" else "20260912_000000_test")
    if difference == "migration":
        path = next((b / "app/db/alembic/versions").glob("*.py"))
        path.write_text(path.read_text() + "# changed upgrade code\n")
    elif difference == "orm":
        (b / "app/db/models.py").write_text("# changed schema\n")
    with pytest.raises(ValueError, match="mismatch|differ"):
        check(db, a, b)
    assert db.read_bytes() == before


def test_missing_database_is_not_created(tmp_path):
    db = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        check(db, tmp_path, tmp_path)
    assert not db.exists()


def test_sqlite_plan_refuses_second_process_even_with_matching_schema(tmp_path):
    import json

    from scripts.local_database_gate import check_plan

    db = database(tmp_path / "store.db")
    a = release(tmp_path / "a")
    b = release(tmp_path / "b")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "database": str(db),
                "active_release": str(a),
                "active_port": 2455,
                "candidates": {"2455": str(a), "2456": str(b)},
            }
        )
    )
    assert check_plan(plan, 2455)["revision"] == "20260912_000000_test"
    with pytest.raises(ValueError, match="lifetime lock"):
        check_plan(plan, 2456)
