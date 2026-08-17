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


# Snapshots per (account, window shape). Sized so the covering indexes win
# the cost comparison decisively: with only a handful of rows the covering
# index and its non-covering key twin cost within noise of each other on a
# fresh PostgreSQL and the EXPLAIN assertions below flake (observed on
# PostgreSQL 16). Hundreds of rows per account separate the index-only path
# by a wide margin while keeping the seed fast (single bulk INSERT).
_BULK_PLAN_FIXTURE_SNAPSHOTS_PER_WINDOW = 150


async def _seed_bulk_history_plan_fixture(session: AsyncSession) -> None:
    from app.db.models import UsageHistory

    now = utcnow()
    accounts_repo = AccountsRepository(session)
    await accounts_repo.upsert(_make_account("acc1"))
    await accounts_repo.upsert(_make_account("acc2"))

    def _entry(account_id: str, used_percent: float, window: str | None, recorded_at, window_minutes: int):
        return UsageHistory(
            account_id=account_id,
            used_percent=used_percent,
            window=window,
            recorded_at=recorded_at,
            window_minutes=window_minutes,
        )

    entries: list[UsageHistory] = []
    for offset in range(_BULK_PLAN_FIXTURE_SNAPSHOTS_PER_WINDOW):
        # 90-second steps keep every row inside the 5-hour query window.
        recorded_at = now - timedelta(seconds=90 * offset)
        entries.append(_entry("acc1", 10.0 + offset, None, recorded_at, 300))
        entries.append(_entry("acc1", 20.0 + offset, "primary", recorded_at, 300))
        entries.append(_entry("acc1", 30.0 + offset, "secondary", recorded_at, 10080))
        entries.append(_entry("acc2", 40.0 + offset, "primary", recorded_at, 300))
        entries.append(_entry("acc2", 50.0 + offset, "secondary", recorded_at, 10080))
    session.add_all(entries)
    await session.commit()

    await _vacuum_analyze_usage_history(session)


async def _vacuum_analyze_usage_history(session: AsyncSession) -> None:
    """Populate the visibility map so covering-index EXPLAIN tests are deterministic.

    A freshly seeded table has an empty visibility map, so the planner costs
    every Index Only Scan with full heap recheck fetches and can prefer a
    plain Index Scan on a cheaper non-covering key index (observed on a clean
    PostgreSQL 18: idx_usage_window_account_time at cost 8.18 beats the
    covering index at 8.5) — disabling seq/bitmap scans does not force the
    index-only path when that cheaper non-covering index exists. VACUUM marks
    the pages all-visible (and ANALYZE refreshes stats), which is the steady
    production state the covering indexes target.
    """
    # Close the seeding transaction first: an open snapshot can keep VACUUM
    # from marking the freshly inserted pages all-visible.
    await session.commit()
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text("VACUUM (ANALYZE) usage_history"))


@pytest.mark.asyncio
async def test_bulk_history_since_primary_query_plan_is_index_only_postgresql(db_setup):
    """The bulk projections fetch must be servable without heap fetches.

    Sequential and bitmap scans are disabled so the planner has to surface
    its index path for the bulk shape; with the covering payload in place
    and the visibility map populated (the seed fixture runs VACUUM ANALYZE)
    that path must be an Index Only Scan (a bare index scan would prove the
    payload is missing).
    """
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        await _seed_bulk_history_plan_fixture(session)

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
                    FROM usage_history
                    WHERE account_id IN ('acc1', 'acc2')
                      AND recorded_at >= now() - interval '5 hours'
                      AND coalesce("window", 'primary') = 'primary'
                    ORDER BY account_id, recorded_at ASC
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "Index Only Scan" in plan_json
    assert (
        "idx_usage_window_account_time_covering" in plan_json
        or "idx_usage_window_raw_account_time_covering" in plan_json
    )
    assert "Seq Scan" not in plan_json


@pytest.mark.asyncio
async def test_bulk_history_since_cutoff_query_plan_is_index_only_postgresql(db_setup):
    """The per-account-cutoff OR shape has an index-only path available.

    With bitmap scans enabled the planner may still prefer a BitmapOr over
    the covering index arms (bitmap heap scans cannot be index-only); this
    pins that the covering payload at least makes the heap-free plan
    available for the production shape.
    """
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        await _seed_bulk_history_plan_fixture(session)

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
                    FROM usage_history
                    WHERE ((account_id = 'acc1' AND recorded_at >= now() - interval '5 hours')
                        OR (account_id = 'acc2' AND recorded_at >= now() - interval '7 days'))
                      AND coalesce("window", 'primary') = 'primary'
                    ORDER BY account_id, recorded_at ASC
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "Index Only Scan" in plan_json
    assert (
        "idx_usage_window_account_time_covering" in plan_json
        or "idx_usage_window_raw_account_time_covering" in plan_json
    )
    assert "Seq Scan" not in plan_json


@pytest.mark.asyncio
async def test_bulk_history_since_secondary_query_plan_is_index_only_postgresql(db_setup):
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        await _seed_bulk_history_plan_fixture(session)

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
                    FROM usage_history
                    WHERE account_id IN ('acc1', 'acc2')
                      AND recorded_at >= now() - interval '7 days'
                      AND "window" = 'secondary'
                    ORDER BY account_id, recorded_at ASC
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "Index Only Scan" in plan_json
    assert "idx_usage_window_raw_account_time_covering" in plan_json
    assert "Seq Scan" not in plan_json


@pytest.mark.asyncio
async def test_bulk_history_since_covered_read_matches_non_covered_read_postgresql(db_setup):
    """The covering index changes the plan, never the rows.

    Pins the spec's row-equality clause by running the production
    ``bulk_history_since`` read once with the index-only path available and
    once with index-only scans disabled (the pre-covering heap-fetch plan),
    then asserting identical results.
    """
    since = utcnow() - timedelta(hours=5)
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only covered-read equality test")

        await _seed_bulk_history_plan_fixture(session)

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        covered = await UsageRepository(session).bulk_history_since(["acc1", "acc2"], "primary", since)

    async with SessionLocal() as session:
        await session.execute(text("SET enable_indexonlyscan = off"))
        non_covered = await UsageRepository(session).bulk_history_since(["acc1", "acc2"], "primary", since)

    # The query orders by (account_id, recorded_at) only, so rows tied on
    # recorded_at (NULL-window and 'primary' snapshots share timestamps) come
    # back in plan-dependent order; compare with a deterministic tie-break.
    def _sorted_rows(grouped):
        return {
            account_id: sorted(snapshots, key=lambda snapshot: (snapshot.recorded_at, snapshot.id))
            for account_id, snapshots in grouped.items()
        }

    assert _sorted_rows(covered) == _sorted_rows(non_covered)
    assert set(covered) == {"acc1", "acc2"}
    # NULL-window + 'primary' snapshots for acc1, 'primary' only for acc2.
    assert len(covered["acc1"]) == 2 * _BULK_PLAN_FIXTURE_SNAPSHOTS_PER_WINDOW
    assert len(covered["acc2"]) == _BULK_PLAN_FIXTURE_SNAPSHOTS_PER_WINDOW


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


@pytest.mark.asyncio
async def test_bulk_history_since_per_account_cutoffs_parity(db_setup):
    """Per-account cutoffs bound the PostgreSQL fetch without changing what
    callers keep after their own trimming; SQLite keeps the shared floor."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc-short"))
        await accounts_repo.upsert(_make_account("acc-wide"))

        # acc-short: rows inside and outside its 5h cutoff.
        await repo.add_entry("acc-short", 10.0, window="primary", recorded_at=now - timedelta(hours=20))
        await repo.add_entry("acc-short", 20.0, window="primary", recorded_at=now - timedelta(hours=1))
        # acc-wide: an old row that only its wide cutoff keeps.
        await repo.add_entry("acc-wide", 30.0, window="primary", recorded_at=now - timedelta(hours=20))
        await repo.add_entry("acc-wide", 40.0, window="primary", recorded_at=now - timedelta(hours=1))

        shared_floor = now - timedelta(days=7)
        cutoffs = {
            "acc-short": now - timedelta(hours=5),
            "acc-wide": now - timedelta(days=7),
        }
        bounded = await repo.bulk_history_since(
            ["acc-short", "acc-wide"],
            "primary",
            shared_floor,
            cutoffs=cutoffs,
        )
        unbounded = await repo.bulk_history_since(["acc-short", "acc-wide"], "primary", shared_floor)

    dialect = "postgresql" if str(engine.url).startswith("postgresql") else "sqlite"
    if dialect == "postgresql":
        assert [snapshot.used_percent for snapshot in bounded["acc-short"]] == [20.0]
    else:
        # SQLite keeps the shared floor; callers trim per account.
        assert [snapshot.used_percent for snapshot in bounded["acc-short"]] == [10.0, 20.0]
    assert [snapshot.used_percent for snapshot in bounded["acc-wide"]] == [30.0, 40.0]

    # Parity with the shared-floor fetch after per-account trimming.
    trimmed = [snapshot for snapshot in unbounded["acc-short"] if snapshot.recorded_at >= cutoffs["acc-short"]]
    assert [snapshot.used_percent for snapshot in trimmed] == [20.0]


@pytest.mark.asyncio
async def test_bulk_history_since_per_account_row_cap_keeps_newest_rows(db_setup):
    """The PostgreSQL row cap keeps each account's newest in-cutoff rows in
    oldest-first order; under-cap accounts are unaffected and SQLite ignores
    the cap entirely (snapshot-cache path, like ``cutoffs``)."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc-dense"))
        await accounts_repo.upsert(_make_account("acc-sparse"))

        for offset in range(8):
            await repo.add_entry(
                "acc-dense",
                10.0 + offset,
                window="secondary",
                recorded_at=now - timedelta(minutes=8 - offset),
            )
        await repo.add_entry("acc-sparse", 90.0, window="secondary", recorded_at=now - timedelta(hours=2))
        await repo.add_entry("acc-sparse", 95.0, window="secondary", recorded_at=now - timedelta(hours=1))

        since = now - timedelta(days=7)
        capped = await repo.bulk_history_since(
            ["acc-dense", "acc-sparse"],
            "secondary",
            since,
            per_account_row_cap=3,
        )
        uncapped = await repo.bulk_history_since(["acc-dense", "acc-sparse"], "secondary", since)

    dialect = "postgresql" if str(engine.url).startswith("postgresql") else "sqlite"
    if dialect == "postgresql":
        # Newest three rows, still oldest-first.
        assert [snapshot.used_percent for snapshot in capped["acc-dense"]] == [15.0, 16.0, 17.0]
        assert capped["acc-dense"] == uncapped["acc-dense"][-3:]
    else:
        # SQLite serves the shared-floor snapshot cache; the cap is ignored.
        assert [snapshot.used_percent for snapshot in capped["acc-dense"]] == [
            snapshot.used_percent for snapshot in uncapped["acc-dense"]
        ]
    # Under-cap accounts return their full in-cutoff slice on every backend.
    assert [snapshot.used_percent for snapshot in capped["acc-sparse"]] == [90.0, 95.0]


@pytest.mark.asyncio
async def test_bulk_history_since_row_cap_respects_per_account_cutoffs_postgresql(db_setup):
    """The cap composes with per-account cutoffs: the cutoff bounds the
    lookback first, then the cap keeps the newest rows inside it."""
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only row-cap test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc-short"))
        await accounts_repo.upsert(_make_account("acc-wide"))

        await repo.add_entry("acc-short", 10.0, window="primary", recorded_at=now - timedelta(hours=20))
        await repo.add_entry("acc-short", 20.0, window="primary", recorded_at=now - timedelta(hours=1))
        for offset in range(4):
            await repo.add_entry(
                "acc-wide",
                30.0 + offset,
                window="primary",
                recorded_at=now - timedelta(hours=20 - offset),
            )

        grouped = await repo.bulk_history_since(
            ["acc-short", "acc-wide"],
            "primary",
            now - timedelta(days=7),
            cutoffs={
                "acc-short": now - timedelta(hours=5),
                "acc-wide": now - timedelta(days=7),
            },
            per_account_row_cap=3,
        )

    # acc-short's 20h-old row falls outside its cutoff even though the cap
    # alone would have kept it.
    assert [snapshot.used_percent for snapshot in grouped["acc-short"]] == [20.0]
    # acc-wide keeps only the newest three of its four in-cutoff rows.
    assert [snapshot.used_percent for snapshot in grouped["acc-wide"]] == [31.0, 32.0, 33.0]


@pytest.mark.asyncio
async def test_bulk_history_since_row_cap_exempts_uncapped_recent_floor_postgresql(db_setup):
    """Rows at or after ``uncapped_recent_floor`` bypass the row cap.

    Live ingestion writes per proxied request whenever the usage fingerprint
    moves, so a burst can put more rows inside the pace-smoothing window than
    any fixed cap; the smoothing mean weighs those samples equally, so they
    must all come back. The cap still bounds the older remainder.
    """
    now = utcnow()
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only row-cap test")

        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc-burst"))

        # Six rows inside the floor window (a burst denser than the cap) and
        # four older rows between the cutoff and the floor.
        for offset in range(6):
            await repo.add_entry(
                "acc-burst",
                50.0 + offset,
                window="secondary",
                recorded_at=now - timedelta(minutes=30 - offset),
            )
        for offset in range(4):
            await repo.add_entry(
                "acc-burst",
                10.0 + offset,
                window="secondary",
                recorded_at=now - timedelta(hours=10 - offset),
            )

        grouped = await repo.bulk_history_since(
            ["acc-burst"],
            "secondary",
            now - timedelta(days=7),
            per_account_row_cap=3,
            uncapped_recent_floor=now - timedelta(minutes=60),
        )

    # All six in-floor rows survive despite cap=3; the older tail keeps only
    # its newest three rows; the slice stays oldest-first.
    assert [snapshot.used_percent for snapshot in grouped["acc-burst"]] == [
        11.0,
        12.0,
        13.0,
        50.0,
        51.0,
        52.0,
        53.0,
        54.0,
        55.0,
    ]


@pytest.mark.asyncio
async def test_bulk_history_since_capped_query_plan_is_index_only_postgresql(db_setup):
    """The capped lateral probes must stay heap-free on the covering indexes.

    Each per-account probe descends the covering index backward and stops at
    the cap or cutoff; a plain Index Scan here would mean the probe shape
    lost the covering payload and fetches the heap per row.
    """
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        await _seed_bulk_history_plan_fixture(session)

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT recent.*
                    FROM (VALUES ('acc1', now() - interval '5 hours'),
                                 ('acc2', now() - interval '7 days'))
                         AS account_cutoffs (account_id, cutoff)
                    JOIN LATERAL (
                        SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
                        FROM usage_history
                        WHERE account_id = account_cutoffs.account_id
                          AND recorded_at >= account_cutoffs.cutoff
                          AND "window" = 'secondary'
                        ORDER BY recorded_at DESC, id DESC
                        LIMIT 100
                    ) AS recent ON true
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "Index Only Scan" in plan_json
    assert "idx_usage_window_raw_account_time_covering" in plan_json
    assert "Seq Scan on usage_history" not in plan_json


@pytest.mark.asyncio
async def test_bulk_history_since_capped_floor_query_plan_is_index_only_postgresql(db_setup):
    """The floor-exempt probe shape (uncapped recent branch UNION ALL capped
    older branch) must keep both branches heap-free on the covering index."""
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        await _seed_bulk_history_plan_fixture(session)

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT recent.*
                    FROM (VALUES ('acc1', now() - interval '7 days', now() - interval '4 hours'),
                                 ('acc2', now() - interval '7 days', now() - interval '4 hours'))
                         AS account_cutoffs (account_id, cutoff, uncapped_floor)
                    JOIN LATERAL (
                        (SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
                         FROM usage_history
                         WHERE account_id = account_cutoffs.account_id
                           AND recorded_at >= account_cutoffs.uncapped_floor
                           AND "window" = 'secondary')
                        UNION ALL
                        (SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
                         FROM usage_history
                         WHERE account_id = account_cutoffs.account_id
                           AND recorded_at >= account_cutoffs.cutoff
                           AND recorded_at < account_cutoffs.uncapped_floor
                           AND "window" = 'secondary'
                         ORDER BY recorded_at DESC, id DESC
                         LIMIT 100)
                    ) AS recent ON true
                    """
                )
            )
        ).scalar_one()

    plan_json = json.dumps(plan)
    assert "Index Only Scan" in plan_json
    assert "idx_usage_window_raw_account_time_covering" in plan_json
    assert "Seq Scan on usage_history" not in plan_json
    assert "Index Scan using" not in plan_json


def _legacy_additional_entry(
    account_id: str,
    *,
    quota_key: str,
    limit_name: str,
    metered_feature: str,
    used_percent: float,
    recorded_at: datetime,
    window: str = "primary",
):
    from app.db.models import AdditionalUsageHistory

    return AdditionalUsageHistory(
        account_id=account_id,
        quota_key=quota_key,
        limit_name=limit_name,
        metered_feature=metered_feature,
        window=window,
        used_percent=used_percent,
        recorded_at=recorded_at,
    )


@pytest.mark.asyncio
async def test_additional_latest_by_account_merges_alias_rows(db_setup):
    """Alias-era rows (legacy quota_key, registry-known aliases) merge with
    canonical rows under the newest-first ordering on every backend."""
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))
        await accounts_repo.upsert(_make_account("acc3"))

        # acc1: the limit_name-alias row is newer than the canonical row.
        await repo.add_entry(
            "acc1",
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            quota_key="codex_spark",
            window="primary",
            used_percent=10.0,
            recorded_at=now - timedelta(hours=1),
        )
        session.add(
            _legacy_additional_entry(
                "acc1",
                quota_key="legacy_spark_key",
                limit_name="codex_other",
                metered_feature="legacy_feature",
                used_percent=55.0,
                recorded_at=now,
            )
        )
        # acc2: the canonical row is newer than the metered_feature-alias row.
        session.add(
            _legacy_additional_entry(
                "acc2",
                quota_key="legacy_spark_key",
                limit_name="unrelated_limit",
                metered_feature="codex_bengalfox",
                used_percent=70.0,
                recorded_at=now - timedelta(hours=2),
            )
        )
        await session.commit()
        await repo.add_entry(
            "acc2",
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            quota_key="codex_spark",
            window="primary",
            used_percent=30.0,
            recorded_at=now,
        )
        # acc3: alias-only history.
        session.add(
            _legacy_additional_entry(
                "acc3",
                quota_key="legacy_spark_key",
                limit_name="codex_other",
                metered_feature="legacy_feature",
                used_percent=90.0,
                recorded_at=now - timedelta(minutes=30),
            )
        )
        await session.commit()

        latest = await repo.latest_by_account("codex_spark", "primary")
        assert set(latest.keys()) == {"acc1", "acc2", "acc3"}
        assert latest["acc1"].used_percent == 55.0
        assert latest["acc2"].used_percent == 30.0
        assert latest["acc3"].used_percent == 90.0

        scoped = await repo.latest_by_account("codex_spark", "primary", account_ids=["acc1"])
        assert set(scoped.keys()) == {"acc1"}
        assert scoped["acc1"].used_percent == 55.0

        recent = await repo.latest_by_account(
            "codex_spark",
            "primary",
            since=now - timedelta(minutes=45),
        )
        assert set(recent.keys()) == {"acc1", "acc2", "acc3"}
        older_only = await repo.latest_by_account(
            "codex_spark",
            "primary",
            account_ids=["acc2"],
            since=now - timedelta(hours=3),
        )
        assert older_only["acc2"].used_percent == 30.0


@pytest.mark.asyncio
async def test_additional_latest_by_account_postgres_uses_top1_probes(db_setup):
    now = utcnow()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only SQL shape test")

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
            used_percent=20.0,
            recorded_at=now,
        )
        await repo.add_entry(
            "acc2",
            limit_name="GPT-5.3-Codex-Spark",
            metered_feature="codex_bengalfox",
            quota_key="codex_spark",
            window="primary",
            used_percent=40.0,
            recorded_at=now,
        )

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            latest = await repo.latest_by_account("codex_spark", "primary")
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

    assert latest["acc1"].used_percent == 20.0
    assert latest["acc2"].used_percent == 40.0
    emitted_sql = "\n".join(statements).lower()
    assert "distinct on" not in emitted_sql
    assert "row_number" not in emitted_sql
    assert "lateral" in emitted_sql


@pytest.mark.asyncio
async def test_list_quota_keys_postgres_loose_scan_matches_distinct(db_setup):
    now = utcnow()
    statements: list[str] = []

    def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only loose scan test")

        accounts_repo = AccountsRepository(session)
        repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1"))
        await accounts_repo.upsert(_make_account("acc2"))
        for offset_minutes in range(5):
            await repo.add_entry(
                "acc1",
                limit_name="GPT-5.3-Codex-Spark",
                metered_feature="codex_bengalfox",
                quota_key="codex_spark",
                window="primary",
                used_percent=10.0 + offset_minutes,
                recorded_at=now - timedelta(minutes=offset_minutes),
            )
        session.add(
            _legacy_additional_entry(
                "acc2",
                quota_key="legacy_other_key",
                limit_name="legacy_other_key",
                metered_feature="legacy_other_feature",
                used_percent=5.0,
                recorded_at=now,
            )
        )
        await session.commit()

        event.listen(engine.sync_engine, "before_cursor_execute", capture_statement)
        try:
            all_keys = await repo.list_quota_keys()
            scoped_keys = await repo.list_quota_keys(account_ids=["acc1"])
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", capture_statement)

        since_keys = await repo.list_quota_keys(since=now - timedelta(minutes=1))

    assert all_keys == sorted({"codex_spark", "legacy_other_key"})
    assert scoped_keys == ["codex_spark"]
    assert "codex_spark" in since_keys
    emitted_sql = "\n".join(statements).lower()
    assert "distinct" not in emitted_sql
