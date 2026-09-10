"""Exercise populated receipt, spool, guest and user migration joins."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tests.integration.retry_claim_migration_helpers import (
    _assert_parent_rows_preserved,
    _cli,
    _downgrade_to_parent,
    _revisions,
    _seed_branch,
    _seed_branch_values,
    _seed_invite,
    _seed_users,
    _state,
)

pytestmark = pytest.mark.integration

_RECEIPTS = "20260910_040000_merge_retry_claim_and_request_log_heads"
_SPOOL = "20260910_010000_dashboard_spool_retention"
_PARENTS = (_RECEIPTS, _SPOOL)
_MERGE = "20260910_160000_merge_retry_claim_spool_heads"
_GUEST = "20260908_000000_add_guest_session_generation"
_GUEST_MERGE = "20260910_170000_merge_guest_retry_claim_heads"
_GUEST_PARENTS = (_MERGE, _GUEST)
_USERS = "20260909_030000_add_audit_actor_columns"
_USERS_MERGE = "20260910_200000_merge_users_retry_claim_heads"
_USERS_PARENTS = (_GUEST_MERGE, _USERS)
_INVITES = "20260909_040000_add_dashboard_user_invites"
_CURRENT_MERGE = "20260910_210000_merge_invite_retry_claim_heads"
_CURRENT_PARENTS = (_USERS_MERGE, _INVITES)
_RETENTION = "http_responses_session_bridge_operation_spool_retention_seconds"


@pytest.mark.parametrize("parent", _PARENTS, ids=["receipt-parent", "spool-parent"])
def test_historical_merge_upgrade_and_downgrade_preserve_populated_parents(tmp_path: Path, parent: str) -> None:
    path = tmp_path / "receipt-spool.sqlite"
    _cli(path, "upgrade", parent)
    assert _revisions(path) == (parent,)
    _seed_branch(path, parent)
    before = _state(path)

    assert f"current_revision={_MERGE}" in _cli(path, "upgrade", _MERGE)
    assert _revisions(path) == (_MERGE,)
    merged = _state(path)
    _assert_parent_rows_preserved(before, merged)
    assert _RETENTION in merged["rows"]["dashboard_settings"][0]
    retry = merged["rows"]["http_bridge_retry_circuits"][0]
    assert {
        "admission_claimed_at_epoch",
        "admission_claimed_generation",
        "admission_claimed_until_epoch",
    } <= retry.keys()

    # Populate the other branch too. A live receipt must survive this no-op downgrade.
    _seed_branch_values(path, _SPOOL if parent == _RECEIPTS else _RECEIPTS)
    populated = _state(path)
    assert populated["rows"]["dashboard_settings"][0][_RETENTION] == 98765.5
    assert populated["rows"]["http_bridge_retry_circuits"][0]["admission_claimed_until_epoch"] == 4102444800.0
    _downgrade_to_parent(path, parent)
    assert _revisions(path) == tuple(sorted(_PARENTS))
    assert _state(path) == populated
    assert f"current_revision={_MERGE}" in _cli(path, "upgrade", _MERGE)
    assert _revisions(path) == (_MERGE,)
    assert _state(path) == populated
    assert f"current_revision={_CURRENT_MERGE}" in _cli(path, "upgrade", "head")
    assert _revisions(path) == (_CURRENT_MERGE,)
    _assert_parent_rows_preserved(populated, _state(path))
    assert "schema_drift=none" in _cli(path, "check")


@pytest.mark.parametrize("parent", _GUEST_PARENTS, ids=["receipt-spool-parent", "guest-parent"])
def test_historical_guest_merge_and_downgrade_preserve_populated_parents(tmp_path: Path, parent: str) -> None:
    path = tmp_path / "guest-receipt.sqlite"
    _cli(path, "upgrade", parent)
    assert _revisions(path) == (parent,)
    _seed_branch(path, _RECEIPTS if parent == _MERGE else _GUEST)
    _seed_branch_values(path, _SPOOL)
    before = _state(path)

    assert f"current_revision={_GUEST_MERGE}" in _cli(path, "upgrade", _GUEST_MERGE)
    assert _revisions(path) == (_GUEST_MERGE,)
    merged = _state(path)
    _assert_parent_rows_preserved(before, merged)
    settings = merged["rows"]["dashboard_settings"][0]
    retry = merged["rows"]["http_bridge_retry_circuits"][0]
    assert settings[_RETENTION] == 98765.5
    assert settings["guest_session_generation"] == (0 if parent == _MERGE else 17)
    for column, value in (
        ("admission_claimed_at_epoch", 1201.25),
        ("admission_claimed_generation", 7),
        ("admission_claimed_until_epoch", 4102444800.0),
    ):
        assert retry[column] == (value if parent == _MERGE else None)

    _seed_branch_values(path, _GUEST if parent == _MERGE else _RECEIPTS)
    populated = _state(path)
    assert populated["rows"]["dashboard_settings"][0]["guest_session_generation"] == 17
    assert populated["rows"]["http_bridge_retry_circuits"][0]["admission_claimed_until_epoch"] == 4102444800.0
    for downgrade_parent in _GUEST_PARENTS:
        _downgrade_to_parent(path, downgrade_parent)
        assert _revisions(path) == tuple(sorted(_GUEST_PARENTS))
        assert _state(path) == populated
        assert f"current_revision={_GUEST_MERGE}" in _cli(path, "upgrade", _GUEST_MERGE)
        assert _revisions(path) == (_GUEST_MERGE,)
        assert _state(path) == populated
    assert f"current_revision={_CURRENT_MERGE}" in _cli(path, "upgrade", "head")
    assert _revisions(path) == (_CURRENT_MERGE,)
    _assert_parent_rows_preserved(populated, _state(path))
    assert "schema_drift=none" in _cli(path, "check")


@pytest.mark.parametrize("parent", _USERS_PARENTS, ids=["guest-retry-parent", "users-parent"])
def test_historical_users_merge_and_downgrade_preserve_populated_parents(tmp_path: Path, parent: str) -> None:
    path = tmp_path / "users-receipt.sqlite"
    _cli(path, "upgrade", parent)
    assert _revisions(path) == (parent,)
    _seed_branch(path, _RECEIPTS if parent == _GUEST_MERGE else _GUEST)
    _seed_branch_values(path, _SPOOL)
    _seed_branch_values(path, _GUEST)
    if parent == _USERS:
        _seed_users(path)
    else:
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE dashboard_settings SET password_hash = 'legacy-admin-hash' WHERE id = 1")
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO audit_logs (id, timestamp, action, details) "
            "VALUES (700, '2026-09-10 12:00:00', 'settings.updated', 'retained-audit')"
        )
        if parent == _USERS:
            connection.execute(
                "UPDATE audit_logs SET timestamp = '2026-09-10 12:00:00.000000', "
                "actor_user_id = 'retained-user', actor_username = 'retained-user', "
                "target_type = 'settings', target_id = 'dashboard', severity = 'warning' WHERE id = 700"
            )
    before = _state(path)

    assert f"current_revision={_USERS_MERGE}" in _cli(path, "upgrade", _USERS_MERGE)
    assert _revisions(path) == (_USERS_MERGE,)
    merged = _state(path)
    if parent == _GUEST_MERGE:
        before["rows"]["audit_logs"][0]["timestamp"] += ".000000"
    _assert_parent_rows_preserved(before, merged)
    audit = merged["rows"]["audit_logs"][0]
    assert audit["actor_user_id"] == ("retained-user" if parent == _USERS else None)
    assert audit["target_id"] == ("dashboard" if parent == _USERS else None)
    assert audit["severity"] == ("warning" if parent == _USERS else "info")
    assert {row["slug"] for row in merged["rows"]["dashboard_roles"] if row["kind"] == "preset"} == {
        "admin",
        "operator",
        "member",
        "viewer",
        "guest",
    }
    retry = merged["rows"]["http_bridge_retry_circuits"][0]
    for column, value in (
        ("admission_claimed_at_epoch", 1201.25),
        ("admission_claimed_generation", 7),
        ("admission_claimed_until_epoch", 4102444800.0),
    ):
        assert retry[column] == (value if parent == _GUEST_MERGE else None)
    if parent == _GUEST_MERGE:
        admin = next(row for row in merged["rows"]["dashboard_users"] if row["username"] == "admin")
        assert admin["password_hash"] == "legacy-admin-hash" and admin["is_break_glass"] == 1
        _seed_users(path)
    else:
        _seed_branch_values(path, _RECEIPTS)
    populated = _state(path)
    for downgrade_parent in _USERS_PARENTS:
        _downgrade_to_parent(path, downgrade_parent)
        assert _revisions(path) == tuple(sorted(_USERS_PARENTS))
        assert _state(path) == populated
        assert f"current_revision={_USERS_MERGE}" in _cli(path, "upgrade", _USERS_MERGE)
        assert _revisions(path) == (_USERS_MERGE,)
        assert _state(path) == populated
    assert f"current_revision={_CURRENT_MERGE}" in _cli(path, "upgrade", "head")
    assert _revisions(path) == (_CURRENT_MERGE,)
    _assert_parent_rows_preserved(populated, _state(path))
    assert "schema_drift=none" in _cli(path, "check")


@pytest.mark.parametrize("parent", _CURRENT_PARENTS, ids=["users-retry-parent", "invite-parent"])
def test_public_invite_head_upgrade_and_downgrade_preserve_populated_parents(tmp_path: Path, parent: str) -> None:
    path = tmp_path / "invite-receipt.sqlite"
    _cli(path, "upgrade", parent)
    assert _revisions(path) == (parent,)
    _seed_branch(path, _RECEIPTS if parent == _USERS_MERGE else _GUEST)
    _seed_branch_values(path, _SPOOL)
    _seed_branch_values(path, _GUEST)
    _seed_users(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO audit_logs (id, timestamp, action, details, actor_user_id, actor_username, "
            "target_type, target_id, severity) VALUES (700, '2026-09-10 12:00:00.000000', "
            "'settings.updated', 'retained-audit', 'retained-user', 'retained-actor-name', "
            "'settings', 'dashboard', 'warning')"
        )
    if parent == _INVITES:
        _seed_invite(path)
    before = _state(path)
    assert f"current_revision={_CURRENT_MERGE}" in _cli(path, "upgrade", "head")
    assert _revisions(path) == (_CURRENT_MERGE,)
    check = _cli(path, "check")
    assert "migration_policy=ok" in check and "schema_drift=none" in check
    merged = _state(path)
    _assert_parent_rows_preserved(before, merged)
    if parent == _USERS_MERGE:
        assert merged["rows"]["dashboard_user_invites"] == []
        _seed_invite(path)
    else:
        retry = merged["rows"]["http_bridge_retry_circuits"][0]
        assert all(
            retry[column] is None
            for column in (
                "admission_claimed_at_epoch",
                "admission_claimed_generation",
                "admission_claimed_until_epoch",
            )
        )
        _seed_branch_values(path, _RECEIPTS)
    populated = _state(path)
    assert populated["rows"]["http_bridge_retry_circuits"][0]["admission_claimed_until_epoch"] == 4102444800.0
    assert len(populated["rows"]["dashboard_user_invites"]) == 1
    for downgrade_parent in _CURRENT_PARENTS:
        _downgrade_to_parent(path, downgrade_parent)
        assert _revisions(path) == tuple(sorted(_CURRENT_PARENTS))
        assert _state(path) == populated
        assert f"current_revision={_CURRENT_MERGE}" in _cli(path, "upgrade", "head")
        assert _revisions(path) == (_CURRENT_MERGE,)
        assert _state(path) == populated
    assert "schema_drift=none" in _cli(path, "check")
