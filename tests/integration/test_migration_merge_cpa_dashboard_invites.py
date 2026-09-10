"""Preserve populated CPA and invitation branches across their merge."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url
from tests.integration.test_migration_merge_cpa_dashboard_identity import (
    _COMMON,
    _IDENTITY,
    _seed_branch,
    _seed_common,
    _state,
)
from tests.integration.test_migration_merge_cpa_dashboard_identity import (
    _CPA as _CATALOG,
)
from tests.integration.test_migration_merge_cpa_guest import _BranchDatabase, _revisions

pytestmark = pytest.mark.integration

_CPA = "20260910_040000_merge_cpa_dashboard_identity"
_INVITES = "20260909_040000_add_dashboard_user_invites"
_PARENTS = (_CPA, _INVITES)
_MERGE = "20260910_050000_merge_cpa_dashboard_invites"


def test_cpa_dashboard_invites_merge_is_only_head_and_keeps_original_parents(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    assert script.get_heads() == [_MERGE]
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS
    cpa = script.get_revision(_CPA)
    invites = script.get_revision(_INVITES)
    assert cpa is not None and cpa.down_revision == (
        "20260910_030000_merge_cpa_guest_heads",
        "20260909_030000_add_audit_actor_columns",
    )
    assert invites is not None and invites.down_revision == "20260909_030000_add_audit_actor_columns"


def _seed_invites(engine: Engine) -> None:
    with engine.begin() as connection:
        for username, consumed, revoked, sso_only, locked in (
            ("retained-user", None, None, False, True),
            ("admin", "2026-09-10 13:00:00", "2026-09-10 14:00:00", True, False),
        ):
            user_id = connection.execute(
                text("SELECT id FROM dashboard_users WHERE username = :username"), {"username": username}
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO dashboard_user_invites "
                    "(id, user_id, token_hash, expires_at, consumed_at, revoked_at, created_by_user_id, "
                    "sso_only, username_locked, created_at) VALUES "
                    "(:id, :user, :token, '2026-09-11 12:00:00', :consumed, :revoked, "
                    "'deleted-inviter', :sso, :locked, '2026-09-10 12:00:00')"
                ),
                {
                    "id": f"invite-{username}",
                    "user": user_id,
                    "token": b"\x00retained-invite-hash\xff" + username.encode(),
                    "consumed": consumed,
                    "revoked": revoked,
                    "sso": sso_only,
                    "locked": locked,
                },
            )


@pytest.fixture(params=[(_CPA,), (_INVITES,), _PARENTS], ids=["cpa-parent", "invite-parent", "both-parents"])
def branch_database(request: pytest.FixtureRequest, tmp_path: Path, db_setup) -> Iterator[_BranchDatabase]:
    configured_url = get_settings().database_url
    postgres = configured_url.startswith("postgresql+")
    url = configured_url if postgres else f"sqlite+aiosqlite:///{tmp_path / 'cpa-invites.sqlite'}"
    engine = create_engine(to_sync_database_url(url))
    try:
        if postgres:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        run_upgrade(url, _COMMON, bootstrap_legacy=False)
        _seed_common(engine)
        run_upgrade(url, _IDENTITY, bootstrap_legacy=False)
        _seed_branch(engine, _IDENTITY)
        parents = request.param
        for revision in parents:
            run_upgrade(url, revision, bootstrap_legacy=False)
            if revision == _CPA:
                _seed_branch(engine, _CATALOG)
            else:
                _seed_invites(engine)
        assert _revisions(engine) == tuple(sorted(parents))
        yield _BranchDatabase(url, engine, parents)
    finally:
        engine.dispose()


def test_populated_branches_merge_and_downgrade_without_data_loss(branch_database: _BranchDatabase) -> None:
    database = branch_database
    before = _state(database.engine, include_invites=True)
    result = run_upgrade(database.url, "head", bootstrap_legacy=False)
    assert result.current_revision == _MERGE
    assert _revisions(database.engine) == (_MERGE,)
    merged = _state(database.engine, include_invites=True)
    for table, old_state in before.items():
        if table == "model_sources" and _CPA not in database.parents:
            for old, new in zip(old_state["rows"], merged[table]["rows"], strict=True):
                assert {column: new[column] for column in old} == old
            continue
        assert merged[table] == old_state
    source = merged["model_sources"]["rows"][0]
    assert source["catalog_mode"] == ("cli_proxy_api" if _CPA in database.parents else "manual")
    assert source["catalog_refresh_token"] == ("owned-refresh" if _CPA in database.parents else None)
    if _CPA not in database.parents:
        assert source["catalog_next_refresh_at"] is None
    if _INVITES not in database.parents:
        assert merged["dashboard_user_invites"]["rows"] == []
        _seed_invites(database.engine)
    else:
        assert len(merged["dashboard_user_invites"]["rows"]) == 2
    assert check_schema_drift(database.url) == ()
    populated = _state(database.engine, include_invites=True)
    for parent in _PARENTS:
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert _state(database.engine, include_invites=True) == populated
        assert check_schema_drift(database.url) == ()
        result = run_upgrade(database.url, "head", bootstrap_legacy=False)
        assert result.current_revision == _MERGE
        assert _revisions(database.engine) == (_MERGE,)
        assert _state(database.engine, include_invites=True) == populated
        assert check_schema_drift(database.url) == ()
