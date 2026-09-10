"""Preserve rejection evidence and guest revocation across the second merge."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from tests.integration.test_migration_merge_rejection_spool import (
    _MERGE as _REJECTION_MERGE,
)
from tests.integration.test_migration_merge_rejection_spool import (
    _REJECTION,
    _SPOOL,
    _cli,
    _disposable_database,
    _MigrationDatabase,
    _revisions,
    _seed_common,
    _seed_parent,
    _state,
)

pytestmark = pytest.mark.integration

_GUEST = "20260908_000000_add_guest_session_generation"
_MERGE = "20260910_180000_merge_guest_rejection_heads"
_PARENTS = (_REJECTION_MERGE, _GUEST)


@pytest.fixture(
    params=[(_REJECTION_MERGE,), (_GUEST,), _PARENTS], ids=["rejection-merge", "guest-parent", "both-parents"]
)
def branch_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_MigrationDatabase]:
    with _disposable_database(tmp_path) as database:
        run_upgrade(database.url, _SPOOL, bootstrap_legacy=False)
        with database.engine.begin() as connection:
            _seed_common(connection)
            _seed_parent(connection, _SPOOL)
        database.starting_revisions = request.param
        for revision in database.starting_revisions:
            run_upgrade(database.url, revision, bootstrap_legacy=False)
            with database.engine.begin() as connection:
                if revision == _REJECTION_MERGE:
                    _seed_parent(connection, _REJECTION)
                else:
                    connection.execute(text("UPDATE dashboard_settings SET guest_session_generation = 9 WHERE id = 1"))
        assert _revisions(database.engine) == tuple(sorted(database.starting_revisions))
        yield database


def test_guest_rejection_histories_converge_at_one_head(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    (head,) = script.get_heads()
    assert _MERGE in {revision.revision for revision in script.iterate_revisions(head, "base")}
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS
    guest = script.get_revision(_GUEST)
    assert guest is not None and guest.down_revision == _SPOOL
    rejection = script.get_revision(_REJECTION_MERGE)
    assert rejection is not None and rejection.down_revision == (_REJECTION, _SPOOL)


def test_populated_cli_upgrade_preserves_guest_and_rejection(
    branch_database: _MigrationDatabase,
) -> None:
    database = branch_database
    expected_rows = _state(database.engine)["rows"]
    if _REJECTION_MERGE not in database.starting_revisions:
        for row in expected_rows["accounts"]:
            row.update(
                block_generation=0,
                rejected_model=None,
                rejected_service_tier=None,
                probe_claim_token=None,
                probe_claim_expires_at=None,
            )
    if _GUEST not in database.starting_revisions:
        for row in expected_rows["dashboard_settings"]:
            row["guest_session_generation"] = 0

    _cli(database.url, "upgrade", "head")
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert _revisions(database.engine) == (head,)
    assert _state(database.engine)["rows"] == expected_rows
    output = _cli(database.url, "check")
    assert "migration_policy=ok" in output and "schema_drift=none" in output


def test_isolated_guest_rejection_merge_only_downgrades_preserve_both_branches(
    branch_database: _MigrationDatabase,
) -> None:
    database = branch_database
    # Keep the historical roundtrip below the later dashboard-user join.
    _cli(database.url, "upgrade", _MERGE)
    with database.engine.begin() as connection:
        if _REJECTION_MERGE not in database.starting_revisions:
            _seed_parent(connection, _REJECTION)
        if _GUEST not in database.starting_revisions:
            connection.execute(text("UPDATE dashboard_settings SET guest_session_generation = 9 WHERE id = 1"))
    populated = _state(database.engine)
    for parent in _PARENTS:
        at_merge = _state(database.engine)
        merge_drift = check_schema_drift(database.url)
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert _state(database.engine) == at_merge
        assert check_schema_drift(database.url) == merge_drift
        _cli(database.url, "upgrade", _MERGE)
        assert _revisions(database.engine) == (_MERGE,)
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == merge_drift
    _cli(database.url, "upgrade", "head")
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert _revisions(database.engine) == (head,)
    assert _state(database.engine) == populated
    assert "schema_drift=none" in _cli(database.url, "check")
