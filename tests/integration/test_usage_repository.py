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
    UsageHistorySnapshot,
    UsageRepository,
    _bulk_history_fingerprint_sqlite,
    _bulk_history_since_sqlite,
    _clear_bulk_history_since_sqlite_cache,
    _fingerprint_grouped_history,
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


def test_bulk_history_since_sqlite_cache_rebuilds_after_delete_and_id_reuse(tmp_path):
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


def test_bulk_history_since_sqlite_cache_rebuilds_after_offsetting_row_corrections(tmp_path):
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
        conn.execute("update usage_history set used_percent = 15.0 where id = 1")
        conn.execute("update usage_history set used_percent = 15.0 where id = 2")
        conn.commit()

    second = _bulk_history_since_sqlite(
        str(db_path),
        ["acc1"],
        "secondary",
        datetime(2026, 1, 1, 0, 0, 0),
    )

    assert [row.used_percent for row in second["acc1"]] == [15.0, 15.0]

    _clear_bulk_history_since_sqlite_cache()


def test_bulk_history_sqlite_fingerprint_normalizes_recorded_at_text(tmp_path):
    db_path = tmp_path / "usage.db"
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
            values (?, ?, ?, ?, ?, ?, ?)
            """,
            (1, "acc1", 10.0, "2026-01-01 00:00:00.000000", 1000.0, 10080, "secondary"),
        )
        conn.commit()

    grouped = {
        "acc1": [
            UsageHistorySnapshot(
                id=1,
                account_id="acc1",
                used_percent=10.0,
                recorded_at=datetime(2026, 1, 1, 0, 0, 0),
                reset_at=1000.0,
                window_minutes=10080,
            )
        ]
    }

    with sqlite3.connect(db_path) as conn:
        sqlite_fingerprint = _bulk_history_fingerprint_sqlite(
            conn,
            ["acc1"],
            "secondary",
            datetime(2026, 1, 1, 0, 0, 0),
        )

    assert sqlite_fingerprint == _fingerprint_grouped_history(grouped)


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
