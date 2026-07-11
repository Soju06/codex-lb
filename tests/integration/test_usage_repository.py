from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import (
    AdditionalUsageRepository,
    UsageRepository,
    _additional_latest_by_account_sqlite,
    _bulk_history_since_sqlite,
    _clear_bulk_history_since_sqlite_cache,
    _latest_by_account_sqlite,
    _resolve_additional_quota_query_scope,
)

pytestmark = pytest.mark.integration


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _dialect_name(session: AsyncSession) -> str:
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else "sqlite"


class _TrackedSqliteConnection:
    def __init__(self, conn: sqlite3.Connection, closed: list[bool]) -> None:
        self._conn = conn
        self._closed = closed

    def close(self) -> None:
        self._closed.append(True)
        self._conn.close()

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def _track_sqlite_connect_close(monkeypatch):
    closed: list[bool] = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        return _TrackedSqliteConnection(original_connect(*args, **kwargs), closed)

    monkeypatch.setattr(sqlite3, "connect", connect)
    return closed


@pytest.mark.asyncio
async def test_latest_by_account_returns_single_latest_per_account(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window="primary", recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 30.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 50.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 20.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="primary", recorded_at=now)

        latest = await repo.latest_by_account(window="primary")
        assert set(latest.keys()) == {"acc1", "acc2"}
        assert latest["acc1"].used_percent == 50.0
        assert latest["acc2"].used_percent == 40.0


@pytest.mark.asyncio
async def test_latest_by_account_respects_window_filter(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        await repo.add_entry("acc1", 10.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 80.0, window="secondary", recorded_at=now)

        primary = await repo.latest_by_account(window="primary")
        assert "acc1" in primary
        assert primary["acc1"].used_percent == 10.0

        secondary = await repo.latest_by_account(window="secondary")
        assert "acc1" in secondary
        assert secondary["acc1"].used_percent == 80.0


@pytest.mark.asyncio
async def test_latest_by_account_default_includes_primary_and_none(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 15.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 25.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 35.0, window=None, recorded_at=now)

        latest = await repo.latest_by_account()
        assert set(latest.keys()) == {"acc1", "acc2"}
        assert latest["acc1"].used_percent == 25.0
        assert latest["acc2"].used_percent == 35.0


@pytest.mark.asyncio
async def test_latest_by_account_uses_recorded_at_with_deterministic_tie_breaker(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc1", 30.0, window="primary", recorded_at=now)
        await repo.add_entry("acc1", 5.0, window="primary", recorded_at=now - timedelta(hours=6))

        latest = await repo.latest_by_account(window="primary")
        assert latest["acc1"].used_percent == 30.0


@pytest.mark.asyncio
async def test_latest_by_account_sqlite_avoids_window_function_for_latest_rows(db_setup):
    now = utcnow()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with SessionLocal() as session:
        if _dialect_name(session) != "sqlite":
            pytest.skip("SQLite-only SQL shape test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))
        await repo.add_entry("acc1", 10.0, window=None, recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 30.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="primary", recorded_at=now)

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            latest = await repo.latest_by_account(window="primary")
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert set(latest.keys()) == {"acc1", "acc2"}
    emitted_sql = "\n".join(statements).lower()
    assert "row_number" not in emitted_sql
    assert " over " not in emitted_sql


@pytest.mark.asyncio
async def test_additional_latest_by_account_sqlite_avoids_window_function_for_latest_rows(db_setup):
    now = utcnow()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with SessionLocal() as session:
        if _dialect_name(session) != "sqlite":
            pytest.skip("SQLite-only SQL shape test")

        accounts_repo = AccountsRepository(session)
        repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))
        await repo.add_entry(
            "acc1",
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            quota_key="codex_spark",
            window="primary",
            used_percent=10.0,
            recorded_at=now - timedelta(hours=1),
        )
        await repo.add_entry(
            "acc1",
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            quota_key="codex_spark",
            window="primary",
            used_percent=20.0,
            recorded_at=now,
        )
        await repo.add_entry(
            "acc2",
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            quota_key="codex_spark",
            window="primary",
            used_percent=30.0,
            recorded_at=now,
        )

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            latest = await repo.latest_by_account("codex_spark", "primary")
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert set(latest.keys()) == {"acc1", "acc2"}
    assert latest["acc1"].used_percent == 20.0
    assert latest["acc2"].used_percent == 30.0
    emitted_sql = "\n".join(statements).lower()
    assert "row_number" not in emitted_sql
    assert " over " not in emitted_sql


@pytest.mark.asyncio
async def test_latest_by_account_primary_query_plan_uses_normalized_window_index(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "sqlite":
            pytest.skip("SQLite-only query plan test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window=None, recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 30.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="secondary", recorded_at=now)

        plan_rows = (
            await session.execute(
                text(
                    """
                    EXPLAIN QUERY PLAN
                    SELECT uh.id
                    FROM usage_history AS uh
                    JOIN (
                        SELECT id AS usage_id,
                               row_number() OVER (
                                   PARTITION BY account_id
                                   ORDER BY recorded_at DESC, id DESC
                               ) AS row_number
                        FROM usage_history
                        WHERE coalesce("window", 'primary') = 'primary'
                    ) AS ranked ON uh.id = ranked.usage_id
                    WHERE ranked.row_number = 1
                    """
                )
            )
        ).fetchall()

    details = " ".join(str(row[-1]) for row in plan_rows)
    assert "idx_usage_window_account_latest" in details


@pytest.mark.asyncio
async def test_latest_by_account_secondary_query_plan_uses_raw_window_index(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "sqlite":
            pytest.skip("SQLite-only query plan test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window="secondary", recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 20.0, window="secondary", recorded_at=now)
        await repo.add_entry("acc2", 30.0, window="primary", recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="secondary", recorded_at=now)

        plan_rows = (
            await session.execute(
                text(
                    """
                    EXPLAIN QUERY PLAN
                    SELECT uh.id
                    FROM usage_history AS uh
                    JOIN (
                        SELECT id AS usage_id,
                               row_number() OVER (
                                   PARTITION BY account_id
                                   ORDER BY recorded_at DESC, id DESC
                               ) AS row_number
                        FROM usage_history
                        WHERE "window" = 'secondary'
                    ) AS ranked ON uh.id = ranked.usage_id
                    WHERE ranked.row_number = 1
                    """
                )
            )
        ).fetchall()

    details = " ".join(str(row[-1]) for row in plan_rows)
    assert "idx_usage_window_raw_account_latest" in details


@pytest.mark.asyncio
async def test_latest_by_account_primary_query_plan_uses_normalized_window_index_postgresql(db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))

        await repo.add_entry("acc1", 10.0, window=None, recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc1", 20.0, window="primary", recorded_at=now)
        await repo.add_entry("acc2", 30.0, window=None, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc2", 40.0, window="secondary", recorded_at=now)

        await session.execute(text("SET enable_seqscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT DISTINCT ON (account_id) id
                    FROM usage_history
                    WHERE coalesce("window", 'primary') = 'primary'
                    ORDER BY account_id ASC, recorded_at DESC, id DESC
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "idx_usage_window_account_latest" in plan_json or "idx_usage_window_account_time" in plan_json
    assert "Seq Scan" not in plan_json


def test_bulk_history_since_sqlite_cache_reuses_superset_and_picks_up_appends(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
                (3, "acc2", 30.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1", "acc2"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.id for row in first["acc1"]] == [1, 2]

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (4, "acc1", 40.0, "2026-01-01 00:02:00", 1000.0, 10080, "secondary"),
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1", "acc2"],
        "secondary",
        datetime(2026, 1, 1, 0, 1, 0),
    )
    assert [row.id for row in second["acc1"]] == [2, 4]
    assert [row.id for row in second["acc2"]] == [3]

    _clear_bulk_history_since_sqlite_cache()


def test_latest_by_account_sqlite_closes_direct_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table accounts (id text primary key)")
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                recorded_at text not null,
                window text,
                used_percent real not null,
                input_tokens integer,
                output_tokens integer,
                reset_at integer,
                window_minutes integer,
                credits_has integer,
                credits_unlimited integer,
                credits_balance real
            )
            """
        )
        conn.execute("insert into accounts (id) values ('acc1')")
        conn.execute(
            """
            insert into usage_history
                (id, account_id, recorded_at, window, used_percent)
            values (1, 'acc1', '2026-01-01 00:00:00', 'primary', 10.0)
            """
        )
        conn.commit()

    closed = _track_sqlite_connect_close(monkeypatch)

    result = _latest_by_account_sqlite(str(db_path), "primary", None)

    assert result["acc1"].used_percent == 10.0
    assert closed == [True]


def test_additional_latest_by_account_sqlite_closes_direct_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table additional_usage_history (
                id integer primary key,
                account_id text not null,
                quota_key text not null,
                limit_name text not null,
                metered_feature text not null,
                window text not null,
                used_percent real not null,
                reset_at integer,
                window_minutes integer,
                recorded_at text not null
            )
            """
        )
        conn.execute(
            """
            insert into additional_usage_history
                (id, account_id, quota_key, limit_name, metered_feature, window, used_percent, recorded_at)
            values (1, 'acc1', 'codex_spark', 'Codex Spark', 'codex_spark', 'primary', 20.0,
                    '2026-01-01 00:00:00')
            """
        )
        conn.commit()
    scope = _resolve_additional_quota_query_scope(quota_key="codex_spark")
    assert scope is not None
    closed = _track_sqlite_connect_close(monkeypatch)

    result = _additional_latest_by_account_sqlite(str(db_path), scope, "primary", None, None)

    assert result["acc1"].used_percent == 20.0
    assert closed == [True]


def test_bulk_history_since_sqlite_closes_direct_connection(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.execute(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (1, 'acc1', 30.0, '2026-01-01 00:00:00', 1000.0, 10080, 'secondary')
            """
        )
        conn.commit()
    closed = _track_sqlite_connect_close(monkeypatch)

    result = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.id for row in result["acc1"]] == [1]
    assert closed == [True]
    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_hit_does_not_materialize_cached_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    statements: list[str] = []
    original_connect = sqlite3.connect

    def connect_with_trace(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(lambda statement: statements.append(" ".join(statement.lower().split())))
        return conn

    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    monkeypatch.setattr(sqlite3, "connect", connect_with_trace)
    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 1, 0),
    )

    assert [row.id for row in second["acc1"]] == [2]
    cached_window_scans = [
        statement
        for statement in statements
        if "select id, account_id, used_percent, recorded_at, reset_at, window_minutes" in statement
        and "id <= ?" in statement
    ]
    assert cached_window_scans == []

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_empty_cache_hit_does_not_materialize_rows(tmp_path, monkeypatch):
    db_path = tmp_path / "usage.db"
    statements: list[str] = []
    original_connect = sqlite3.connect

    def connect_with_trace(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(lambda statement: statements.append(" ".join(statement.lower().split())))
        return conn

    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc_empty"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert first == {}

    monkeypatch.setattr(sqlite3, "connect", connect_with_trace)
    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc_empty"],
        "secondary",
        datetime(2026, 1, 1, 0, 1, 0),
    )

    assert second == {}
    full_history_refreshes = [
        statement
        for statement in statements
        if "select id, account_id, used_percent, recorded_at, reset_at, window_minutes" in statement
        and "order by account_id, recorded_at asc" in statement
        and "id > 0" not in statement
    ]
    assert full_history_refreshes == []

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_detects_same_id_corrections(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.used_percent for row in first["acc1"]] == [10.0, 20.0]

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            update usage_history
            set used_percent = ?,
                reset_at = ?,
                window_minutes = ?
            where id = ?
            """,
            [
                (15.0, 1000.0, 10080, 1),
                (15.0, 2000.0, 432000, 2),
            ],
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.id for row in second["acc1"]] == [1, 2]
    assert [row.used_percent for row in second["acc1"]] == [15.0, 15.0]
    assert second["acc1"][1].reset_at == 2000.0
    assert second["acc1"][1].window_minutes == 432000

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_detects_offsetting_corrections(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
                (3, "acc1", 30.0, "2026-01-01 00:02:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.used_percent for row in first["acc1"]] == [10.0, 20.0, 30.0]

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            update usage_history
            set used_percent = ?
            where id = ?
            """,
            [
                (11.0, 1),
                (18.0, 2),
                (31.0, 3),
            ],
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.used_percent for row in second["acc1"]] == [11.0, 18.0, 31.0]

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_detects_second_moment_collision_corrections(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
                (3, "acc1", 30.0, "2026-01-01 00:02:00", 1000.0, 10080, "secondary"),
                (4, "acc1", 40.0, "2026-01-01 00:03:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.used_percent for row in first["acc1"]] == [10.0, 20.0, 30.0, 40.0]

    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            """
            update usage_history
            set used_percent = ?
            where id = ?
            """,
            [
                (11.0, 1),
                (17.0, 2),
                (33.0, 3),
                (39.0, 4),
            ],
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.used_percent for row in second["acc1"]] == [11.0, 17.0, 33.0, 39.0]

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_since_sqlite_cache_detects_external_delete_and_id_reuse(tmp_path):
    db_path = tmp_path / "usage.db"
    _clear_bulk_history_since_sqlite_cache()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            create table usage_history (
                id integer primary key,
                account_id text not null,
                used_percent real not null,
                recorded_at text not null,
                reset_at real,
                window_minutes integer,
                window text
            )
            """
        )
        conn.executemany(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "acc1", 10.0, "2026-01-01 00:00:00", 1000.0, 10080, "secondary"),
                (2, "acc1", 20.0, "2026-01-01 00:01:00", 1000.0, 10080, "secondary"),
            ],
        )
        conn.commit()

    first = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )
    assert [row.used_percent for row in first["acc1"]] == [10.0, 20.0]

    with sqlite3.connect(db_path) as conn:
        conn.execute("delete from usage_history")
        conn.execute(
            """
            insert into usage_history
                (id, account_id, used_percent, recorded_at, reset_at, window_minutes, window)
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "acc1", 75.0, "2026-01-01 00:02:00", 2000.0, 10080, "secondary"),
        )
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.id for row in second["acc1"]] == [1]
    assert [row.used_percent for row in second["acc1"]] == [75.0]

    _clear_bulk_history_since_sqlite_cache()

    third = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.id for row in third["acc1"]] == [1]
    assert [row.used_percent for row in third["acc1"]] == [75.0]

    _clear_bulk_history_since_sqlite_cache()


@pytest.mark.asyncio
async def test_trends_by_bucket_uses_latest_sample_window_metadata(db_setup):
    recorded_at = datetime(2026, 1, 1, 12, 0, 0)
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))

        await repo.add_entry(
            "acc1",
            10.0,
            window="secondary",
            reset_at=9999,
            window_minutes=10080,
            recorded_at=recorded_at,
        )
        await repo.add_entry(
            "acc1",
            30.0,
            window="secondary",
            reset_at=1111,
            window_minutes=300,
            recorded_at=recorded_at + timedelta(minutes=5),
        )

        trends = await repo.trends_by_bucket(
            since=recorded_at - timedelta(minutes=1),
            bucket_seconds=86400,
            window="secondary",
        )

    assert len(trends) == 1
    assert trends[0].samples == 2
    assert trends[0].avg_used_percent == pytest.approx(20.0)
    assert trends[0].reset_at == 1111
    assert trends[0].window_minutes == 300
    assert trends[0].recorded_at == recorded_at + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_trends_by_bucket_sqlite_avoids_window_function_for_latest_metadata(db_setup):
    recorded_at = datetime(2026, 1, 1, 12, 0, 0)
    statements: list[str] = []

    async with SessionLocal() as session:
        if _dialect_name(session) != "sqlite":
            pytest.skip("SQLite-only SQL shape test")

        bind = session.get_bind()
        assert bind is not None

        def _capture_sql(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement)

        event.listen(bind, "before_cursor_execute", _capture_sql)
        try:
            accounts_repo = AccountsRepository(session)
            repo = UsageRepository(session)
            await accounts_repo.upsert(_make_account("acc1"))

            await repo.add_entry(
                "acc1",
                10.0,
                window="secondary",
                reset_at=9999,
                window_minutes=10080,
                recorded_at=recorded_at,
            )
            await repo.add_entry(
                "acc1",
                30.0,
                window="secondary",
                reset_at=1111,
                window_minutes=300,
                recorded_at=recorded_at + timedelta(minutes=5),
            )

            trends = await repo.trends_by_bucket(
                since=recorded_at - timedelta(minutes=1),
                bucket_seconds=86400,
                window="secondary",
                account_id="acc1",
            )
        finally:
            event.remove(bind, "before_cursor_execute", _capture_sql)

    assert len(trends) == 1
    assert trends[0].samples == 2
    assert trends[0].reset_at == 1111
    assert trends[0].window_minutes == 300

    trend_queries = [
        statement for statement in statements if "usage_history" in statement and "bucket_epoch" in statement
    ]
    assert len(trend_queries) == 1
    assert "row_number()" not in trend_queries[0].lower()
