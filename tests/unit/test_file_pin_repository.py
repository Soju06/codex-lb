from __future__ import annotations

import pytest
from sqlalchemy import String

from app.db.models import FileAccountPin
from app.modules.proxy.file_pin_repository import (
    build_file_account_pin_claim,
    build_file_account_pin_cleanup,
    build_file_account_pin_live_lookup,
    build_file_account_pin_refresh,
)

pytestmark = pytest.mark.unit


def test_file_account_pin_keeps_upstream_file_id_opaque() -> None:
    file_id_type = FileAccountPin.__table__.c.file_id.type
    assert isinstance(file_id_type, String)
    assert file_id_type.length is None


def test_postgresql_file_pin_statements_use_current_database_clock() -> None:
    claim_sql = str(build_file_account_pin_claim(dialect_name="postgresql"))
    conflict_clause = claim_sql.split("DO UPDATE SET", 1)[1]

    assert claim_sql.count("clock_timestamp() + make_interval(secs => :ttl)") == 2
    assert "excluded.expires_at" not in conflict_clause
    assert "expires_at = clock_timestamp() + make_interval(secs => :ttl)" in conflict_clause
    assert "file_account_pins.expires_at <= clock_timestamp()" in conflict_clause
    cleanup_sql = str(build_file_account_pin_cleanup(dialect_name="postgresql"))
    assert "expires_at <= statement_timestamp()" in cleanup_sql
    assert "clock_timestamp()" not in cleanup_sql
    refresh_sql = str(build_file_account_pin_refresh(dialect_name="postgresql"))
    assert "expires_at = clock_timestamp() + make_interval(secs => :ttl)" in refresh_sql
    assert "file_id = :file_id" in refresh_sql
    assert "account_id = :account_id" in refresh_sql
    assert "RETURNING account_id" in refresh_sql
    assert "expires_at > clock_timestamp()" in str(build_file_account_pin_live_lookup(dialect_name="postgresql"))
    assert "expires_at > clock_timestamp()" in str(
        build_file_account_pin_live_lookup(dialect_name="postgresql", many=True)
    )


def test_sqlite_file_pin_statements_use_padded_statement_clock() -> None:
    claim_sql = str(build_file_account_pin_claim(dialect_name="sqlite"))
    sqlite_now = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
    sqlite_now_plus_ttl = "(strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds') || '000')"
    conflict_clause = claim_sql.split("DO UPDATE SET", 1)[1]

    assert claim_sql.count(sqlite_now_plus_ttl) == 2
    assert "excluded.expires_at" not in conflict_clause
    assert f"expires_at = {sqlite_now_plus_ttl}" in conflict_clause
    assert f"file_account_pins.expires_at <= {sqlite_now}" in conflict_clause
    assert sqlite_now in str(build_file_account_pin_cleanup(dialect_name="sqlite"))
    refresh_sql = str(build_file_account_pin_refresh(dialect_name="sqlite"))
    assert sqlite_now_plus_ttl in refresh_sql
    assert "file_id = :file_id" in refresh_sql
    assert "account_id = :account_id" in refresh_sql
    assert "RETURNING account_id" in refresh_sql
    assert sqlite_now in str(build_file_account_pin_live_lookup(dialect_name="sqlite"))
    assert sqlite_now in str(build_file_account_pin_live_lookup(dialect_name="sqlite", many=True))
    assert "CURRENT_TIMESTAMP" not in claim_sql


@pytest.mark.parametrize(
    "builder",
    [
        build_file_account_pin_claim,
        build_file_account_pin_cleanup,
        build_file_account_pin_refresh,
        build_file_account_pin_live_lookup,
    ],
)
def test_file_pin_statement_builders_reject_unknown_dialect(builder) -> None:
    with pytest.raises(RuntimeError, match="Unsupported database dialect"):
        builder(dialect_name="mysql")
