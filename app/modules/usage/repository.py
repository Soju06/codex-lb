from __future__ import annotations

import sqlite3
from collections.abc import Collection
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from threading import RLock
from typing import Any, cast

from anyio import to_thread
from sqlalchemy import Integer, and_, delete, func, literal_column, or_, select, true
from sqlalchemy import cast as sqlalchemy_cast
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.usage.types import UsageAggregateRow, UsageTrendBucket
from app.core.utils.time import utcnow
from app.db.models import Account, AdditionalUsageHistory, UsageHistory
from app.db.session import sqlite_writer_section
from app.db.sqlite_utils import sqlite_db_path_from_url
from app.modules.usage.additional_quota_keys import (
    AdditionalQuotaQueryScope,
    canonicalize_additional_quota_key,
    get_additional_quota_query_scope,
)

_PRIMARY_WINDOW_LITERAL = literal_column("'primary'")


@dataclass(frozen=True, slots=True)
class UsageHistorySnapshot:
    id: int
    account_id: str
    used_percent: float
    recorded_at: datetime
    reset_at: float | None
    window_minutes: int | None


@dataclass(frozen=True, slots=True)
class UsageWindowWrite:
    window: str
    used_percent: float
    reset_at: int | None = None
    window_minutes: int | None = None
    credits_has: bool | None = None
    credits_unlimited: bool | None = None
    credits_balance: float | None = None


@dataclass(frozen=True, slots=True)
class _BulkHistoryCacheMetadata:
    row_count: int
    max_id: int
    content_digest: str


@dataclass(slots=True)
class _BulkHistoryCacheEntry:
    since: datetime
    max_id: int
    metadata: _BulkHistoryCacheMetadata
    rows_by_account: dict[str, list[UsageHistorySnapshot]]


_BULK_HISTORY_SQLITE_CACHE: dict[tuple[str, tuple[str, ...], str], _BulkHistoryCacheEntry] = {}
_BULK_HISTORY_SQLITE_CACHE_LOCK = RLock()
_EMPTY_BULK_HISTORY_DIGEST = sha256().hexdigest()


def _normalized_sqlite_datetime_text(value) -> str:
    return str(_parse_sqlite_datetime(value))


class _BulkHistoryDigestAggregate:
    def __init__(self) -> None:
        self._digest = sha256()

    def step(
        self,
        row_id: int,
        account_id: str,
        used_percent: float,
        recorded_at: str,
        reset_at: float | None,
        window_minutes: int | None,
    ) -> None:
        self._digest.update(str(int(row_id)).encode("utf-8"))
        self._digest.update(b"\x1f")
        account_bytes = str(account_id).encode("utf-8")
        self._digest.update(str(len(account_bytes)).encode("ascii"))
        self._digest.update(b":")
        self._digest.update(account_bytes)
        self._digest.update(b"\x1f")
        self._digest.update(float(used_percent).hex().encode("ascii"))
        self._digest.update(b"\x1f")
        recorded_at_bytes = _normalized_sqlite_datetime_text(recorded_at).encode("utf-8")
        self._digest.update(str(len(recorded_at_bytes)).encode("ascii"))
        self._digest.update(b":")
        self._digest.update(recorded_at_bytes)
        self._digest.update(b"\x1f")
        self._digest.update(b"NULL" if reset_at is None else float(reset_at).hex().encode("ascii"))
        self._digest.update(b"\x1f")
        self._digest.update(b"NULL" if window_minutes is None else str(int(window_minutes)).encode("ascii"))
        self._digest.update(b"\x1e")

    def finalize(self) -> str:
        return self._digest.hexdigest()


def _clear_bulk_history_since_sqlite_cache() -> None:
    with _BULK_HISTORY_SQLITE_CACHE_LOCK:
        _BULK_HISTORY_SQLITE_CACHE.clear()


def _bulk_history_cache_key(
    db_path: str,
    account_ids: list[str],
    window: str,
) -> tuple[str, tuple[str, ...], str]:
    return (db_path, tuple(sorted(account_ids)), window)


def _clone_filtered_history(
    grouped: dict[str, list[UsageHistorySnapshot]],
    since: datetime,
) -> dict[str, list[UsageHistorySnapshot]]:
    filtered_grouped: dict[str, list[UsageHistorySnapshot]] = {}
    for account_id, rows in grouped.items():
        filtered = [row for row in rows if row.recorded_at >= since]
        if filtered:
            filtered_grouped[account_id] = filtered
    return filtered_grouped


def _bulk_history_metadata_from_grouped(
    grouped: dict[str, list[UsageHistorySnapshot]],
) -> _BulkHistoryCacheMetadata:
    digest = sha256()
    rows = sorted((row for rows in grouped.values() for row in rows), key=lambda row: (row.id, row.account_id))
    for row in rows:
        digest.update(str(int(row.id)).encode("utf-8"))
        digest.update(b"\x1f")
        account_bytes = str(row.account_id).encode("utf-8")
        digest.update(str(len(account_bytes)).encode("ascii"))
        digest.update(b":")
        digest.update(account_bytes)
        digest.update(b"\x1f")
        digest.update(float(row.used_percent).hex().encode("ascii"))
        digest.update(b"\x1f")
        recorded_at_bytes = str(row.recorded_at).encode("utf-8")
        digest.update(str(len(recorded_at_bytes)).encode("ascii"))
        digest.update(b":")
        digest.update(recorded_at_bytes)
        digest.update(b"\x1f")
        digest.update(b"NULL" if row.reset_at is None else float(row.reset_at).hex().encode("ascii"))
        digest.update(b"\x1f")
        digest.update(b"NULL" if row.window_minutes is None else str(int(row.window_minutes)).encode("ascii"))
        digest.update(b"\x1e")
    return _BulkHistoryCacheMetadata(
        row_count=len(rows),
        max_id=max((row.id for row in rows), default=0),
        content_digest=digest.hexdigest(),
    )


def _append_grouped_history(
    target: dict[str, list[UsageHistorySnapshot]],
    source: dict[str, list[UsageHistorySnapshot]],
) -> None:
    for account_id, rows in source.items():
        bucket = target.setdefault(account_id, [])
        bucket.extend(rows)
        bucket.sort(key=lambda row: (row.recorded_at, row.id))


def _query_bulk_history_since_sqlite(
    conn: sqlite3.Connection,
    account_ids: list[str],
    window: str,
    since: datetime,
    *,
    after_id: int | None = None,
) -> dict[str, list[UsageHistorySnapshot]]:
    placeholders = ",".join("?" for _ in account_ids)
    since_param = since.isoformat(sep=" ")
    id_clause = ""
    params: list[object]
    if window == "primary":
        window_clause = "coalesce(window, 'primary') = 'primary'"
        params = [*account_ids, since_param]
    else:
        window_clause = "window = ?"
        params = [*account_ids, window, since_param]
    if after_id is not None:
        id_clause = "and id > ?"
        params.append(after_id)
    sql = f"""
        select id, account_id, used_percent, recorded_at, reset_at, window_minutes
        from usage_history
        where account_id in ({placeholders})
          and {window_clause}
          and recorded_at >= ?
          {id_clause}
        order by account_id, recorded_at asc
    """
    grouped: dict[str, list[UsageHistorySnapshot]] = {}
    rows = conn.execute(sql, params)
    for row in rows:
        snapshot = UsageHistorySnapshot(
            id=int(row[0]),
            account_id=str(row[1]),
            used_percent=float(row[2]),
            recorded_at=_parse_sqlite_datetime(row[3]),
            reset_at=float(row[4]) if row[4] is not None else None,
            window_minutes=int(row[5]) if row[5] is not None else None,
        )
        grouped.setdefault(snapshot.account_id, []).append(snapshot)
    return grouped


def _query_bulk_history_metadata_sqlite(
    conn: sqlite3.Connection,
    account_ids: list[str],
    window: str,
    since: datetime,
    *,
    max_id: int | None = None,
) -> _BulkHistoryCacheMetadata:
    placeholders = ",".join("?" for _ in account_ids)
    since_param = since.isoformat(sep=" ")
    id_clause = ""
    params: list[object]
    if window == "primary":
        window_clause = "coalesce(window, 'primary') = 'primary'"
        params = [*account_ids, since_param]
    else:
        window_clause = "window = ?"
        params = [*account_ids, window, since_param]
    if max_id is not None:
        id_clause = "and id <= ?"
        params.append(max_id)
    conn.create_aggregate("clb_bulk_history_digest", 6, cast(Any, _BulkHistoryDigestAggregate))
    sql = f"""
        select count(*),
               coalesce(max(id), 0),
               coalesce(
                   clb_bulk_history_digest(id, account_id, used_percent, recorded_at, reset_at, window_minutes),
                   '{_EMPTY_BULK_HISTORY_DIGEST}'
               )
        from (
            select id, account_id, used_percent, recorded_at, reset_at, window_minutes
            from usage_history
            where account_id in ({placeholders})
              and {window_clause}
              and recorded_at >= ?
              {id_clause}
            order by id asc, account_id asc
        )
    """
    row = conn.execute(sql, params).fetchone()
    return _BulkHistoryCacheMetadata(
        row_count=int(row[0]),
        max_id=int(row[1]),
        content_digest=str(row[2]),
    )


def _normalized_window_expr():
    return func.coalesce(UsageHistory.window, _PRIMARY_WINDOW_LITERAL)


def _window_clause(window: str | None):
    if not window or window == "primary":
        return _normalized_window_expr() == "primary"
    return UsageHistory.window == window


def _sqlite_path_from_bind(bind) -> object | None:
    bind_url = getattr(bind, "url", None)
    if bind_url is not None:
        return sqlite_db_path_from_url(str(bind_url))
    return sqlite_db_path_from_url(get_settings().database_url)


def _parse_sqlite_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _usage_history_from_sqlite_row(row) -> UsageHistory:
    return UsageHistory(
        id=int(row[0]),
        account_id=str(row[1]),
        recorded_at=_parse_sqlite_datetime(row[2]),
        window=row[3],
        used_percent=float(row[4]),
        input_tokens=int(row[5]) if row[5] is not None else None,
        output_tokens=int(row[6]) if row[6] is not None else None,
        reset_at=int(row[7]) if row[7] is not None else None,
        window_minutes=int(row[8]) if row[8] is not None else None,
        credits_has=bool(row[9]) if row[9] is not None else None,
        credits_unlimited=bool(row[10]) if row[10] is not None else None,
        credits_balance=float(row[11]) if row[11] is not None else None,
    )


def _additional_usage_history_from_sqlite_row(row) -> AdditionalUsageHistory:
    return AdditionalUsageHistory(
        id=int(row[0]),
        account_id=str(row[1]),
        quota_key=str(row[2]),
        limit_name=str(row[3]),
        metered_feature=str(row[4]),
        window=str(row[5]),
        used_percent=float(row[6]),
        reset_at=int(row[7]) if row[7] is not None else None,
        window_minutes=int(row[8]) if row[8] is not None else None,
        recorded_at=_parse_sqlite_datetime(row[9]),
    )


def _latest_by_account_sqlite(
    db_path: str,
    window: str | None,
    account_ids: list[str] | None,
) -> dict[str, UsageHistory]:
    if account_ids is None:
        account_sql = "select id from accounts"
        account_params: list[object] = []
    elif not account_ids:
        return {}
    else:
        placeholders = ",".join("?" for _ in account_ids)
        account_sql = f"select id from accounts where id in ({placeholders})"
        account_params = list(account_ids)

    if not window or window == "primary":
        window_clause = "coalesce(window, 'primary') = 'primary'"
        window_params: list[object] = []
    else:
        window_clause = "window = ?"
        window_params = [window]
    latest_sql = f"""
        select id, account_id, recorded_at, window, used_percent,
               input_tokens, output_tokens, reset_at, window_minutes,
               credits_has, credits_unlimited, credits_balance
        from usage_history
        where account_id = ?
          and {window_clause}
        order by recorded_at desc, id desc
        limit 1
    """

    latest: dict[str, UsageHistory] = {}
    with closing(sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        accounts = [str(row[0]) for row in conn.execute(account_sql, account_params)]
        for account_id in accounts:
            row = conn.execute(latest_sql, [account_id, *window_params]).fetchone()
            if row is not None:
                entry = _usage_history_from_sqlite_row(row)
                latest[entry.account_id] = entry
    return latest


def _additional_scope_sqlite_clause(scope: AdditionalQuotaQueryScope) -> tuple[str, list[object]]:
    quota_values = tuple(scope.quota_key_match_values or {scope.quota_key})
    clauses = [f"quota_key in ({','.join('?' for _ in quota_values)})"]
    params: list[object] = list(quota_values)
    if scope.limit_name_match_values:
        clauses.append(f"lower(limit_name) in ({','.join('?' for _ in scope.limit_name_match_values)})")
        params.extend(scope.limit_name_match_values)
    if scope.metered_feature_match_values:
        clauses.append(f"lower(metered_feature) in ({','.join('?' for _ in scope.metered_feature_match_values)})")
        params.extend(scope.metered_feature_match_values)
    return f"({' or '.join(clauses)})", params


def _additional_latest_by_account_sqlite(
    db_path: str,
    scope: AdditionalQuotaQueryScope,
    window: str,
    account_ids: list[str] | None,
    since: datetime | None,
) -> dict[str, AdditionalUsageHistory]:
    scope_clause, scope_params = _additional_scope_sqlite_clause(scope)
    account_filter = ""
    account_params: list[object] = []
    if account_ids is not None:
        if not account_ids:
            return {}
        account_filter = f"and account_id in ({','.join('?' for _ in account_ids)})"
        account_params = list(account_ids)
    since_filter = ""
    since_params: list[object] = []
    if since is not None:
        since_filter = "and recorded_at >= ?"
        since_params = [since.isoformat(sep=" ")]

    accounts_sql = f"""
        select distinct account_id
        from additional_usage_history
        where {scope_clause}
          and window = ?
          {account_filter}
          {since_filter}
    """
    latest_sql = f"""
        select id, account_id, quota_key, limit_name, metered_feature, window,
               used_percent, reset_at, window_minutes, recorded_at
        from additional_usage_history
        where account_id = ?
          and {scope_clause}
          and window = ?
          {since_filter}
        order by recorded_at desc, used_percent desc, id desc
        limit 1
    """

    latest: dict[str, AdditionalUsageHistory] = {}
    with closing(sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        accounts_params = [*scope_params, window, *account_params, *since_params]
        accounts = [str(row[0]) for row in conn.execute(accounts_sql, accounts_params)]
        for account_id in accounts:
            row = conn.execute(latest_sql, [account_id, *scope_params, window, *since_params]).fetchone()
            if row is not None:
                entry = _additional_usage_history_from_sqlite_row(row)
                latest[entry.account_id] = entry
    return latest


def _bulk_history_since_sqlite(
    db_path: str,
    account_ids: list[str],
    window: str,
    since: datetime,
) -> dict[str, list[UsageHistorySnapshot]]:
    cache_key = _bulk_history_cache_key(db_path, account_ids, window)
    with closing(sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        with _BULK_HISTORY_SQLITE_CACHE_LOCK:
            cached = _BULK_HISTORY_SQLITE_CACHE.get(cache_key)
            if cached is not None and cached.since <= since:
                metadata = _query_bulk_history_metadata_sqlite(
                    conn,
                    account_ids,
                    window,
                    cached.since,
                    max_id=cached.max_id,
                )
                if metadata != cached.metadata:
                    grouped = _query_bulk_history_since_sqlite(conn, account_ids, window, cached.since)
                    cached.metadata = _bulk_history_metadata_from_grouped(grouped)
                    cached.max_id = cached.metadata.max_id
                    cached.rows_by_account = grouped
                    return _clone_filtered_history(grouped, since)

                new_rows = _query_bulk_history_since_sqlite(
                    conn,
                    account_ids,
                    window,
                    cached.since,
                    after_id=cached.max_id,
                )
                if new_rows:
                    _append_grouped_history(cached.rows_by_account, new_rows)
                    cached.metadata = _bulk_history_metadata_from_grouped(cached.rows_by_account)
                    cached.max_id = cached.metadata.max_id
                return _clone_filtered_history(cached.rows_by_account, since)

            grouped = _query_bulk_history_since_sqlite(conn, account_ids, window, since)
            metadata = _bulk_history_metadata_from_grouped(grouped)
            _BULK_HISTORY_SQLITE_CACHE[cache_key] = _BulkHistoryCacheEntry(
                since=since,
                max_id=metadata.max_id,
                metadata=metadata,
                rows_by_account=grouped,
            )
            return _clone_filtered_history(grouped, since)


def _resolve_additional_quota_key(
    *,
    quota_key: str | None = None,
    limit_name: str | None = None,
    metered_feature: str | None = None,
) -> str | None:
    candidate_limit_name = quota_key if quota_key is not None else limit_name
    if candidate_limit_name is None and metered_feature is None:
        return None
    return canonicalize_additional_quota_key(
        quota_key=quota_key,
        limit_name=candidate_limit_name,
        metered_feature=metered_feature,
    )


def _resolve_additional_quota_query_scope(
    *,
    quota_key: str | None = None,
    limit_name: str | None = None,
    metered_feature: str | None = None,
) -> AdditionalQuotaQueryScope | None:
    return get_additional_quota_query_scope(
        quota_key=quota_key,
        limit_name=limit_name,
        metered_feature=metered_feature,
    )


def _additional_quota_match_clause(scope: AdditionalQuotaQueryScope, *, canonical_only: bool = False):
    clauses = [AdditionalUsageHistory.quota_key.in_(tuple(scope.quota_key_match_values or {scope.quota_key}))]
    if canonical_only:
        return clauses[0]
    alias_clause = _additional_quota_alias_match_clause(scope)
    if alias_clause is not None:
        clauses.append(alias_clause)
    return or_(*clauses)


def _additional_quota_alias_match_clause(scope: AdditionalQuotaQueryScope):
    clauses = []
    if scope.limit_name_match_values:
        clauses.append(func.lower(AdditionalUsageHistory.limit_name).in_(tuple(scope.limit_name_match_values)))
    if scope.metered_feature_match_values:
        clauses.append(
            func.lower(AdditionalUsageHistory.metered_feature).in_(tuple(scope.metered_feature_match_values))
        )
    if not clauses:
        return None
    return or_(*clauses)


def _newer_additional_usage_entry(
    current: AdditionalUsageHistory | None,
    candidate: AdditionalUsageHistory,
) -> AdditionalUsageHistory:
    if current is None:
        return candidate
    current_key = (
        current.recorded_at,
        current.used_percent,
        current.id,
    )
    candidate_key = (
        candidate.recorded_at,
        candidate.used_percent,
        candidate.id,
    )
    return candidate if candidate_key > current_key else current


def _merge_latest_additional_usage_entries(
    entries: dict[str, AdditionalUsageHistory],
    candidates: Collection[AdditionalUsageHistory],
) -> None:
    for candidate in candidates:
        entries[candidate.account_id] = _newer_additional_usage_entry(
            entries.get(candidate.account_id),
            candidate,
        )


class UsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def latest_entry_for_account(
        self,
        account_id: str,
        *,
        window: str | None = None,
    ) -> UsageHistory | None:
        stmt = (
            select(UsageHistory)
            .where(UsageHistory.account_id == account_id)
            .where(_window_clause(window))
            .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def add_entry(
        self,
        account_id: str,
        used_percent: float,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        recorded_at: datetime | None = None,
        window: str | None = None,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        credits_has: bool | None = None,
        credits_unlimited: bool | None = None,
        credits_balance: float | None = None,
    ) -> UsageHistory:
        entry = UsageHistory(
            account_id=account_id,
            used_percent=used_percent,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            window=window,
            reset_at=reset_at,
            window_minutes=window_minutes,
            credits_has=credits_has,
            credits_unlimited=credits_unlimited,
            credits_balance=credits_balance,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        async with sqlite_writer_section():
            await self._session.commit()
            await self._session.refresh(entry)
        return entry

    async def add_account_snapshot(
        self,
        account_id: str,
        windows: Collection[UsageWindowWrite],
        *,
        recorded_at: datetime | None = None,
    ) -> list[UsageHistory]:
        """Persist one account's standard usage windows atomically."""
        if not windows:
            return []
        captured_at = recorded_at or utcnow()
        entries = [
            UsageHistory(
                account_id=account_id,
                used_percent=window.used_percent,
                input_tokens=None,
                output_tokens=None,
                window=window.window,
                reset_at=window.reset_at,
                window_minutes=window.window_minutes,
                credits_has=window.credits_has,
                credits_unlimited=window.credits_unlimited,
                credits_balance=window.credits_balance,
                recorded_at=captured_at,
            )
            for window in windows
        ]
        self._session.add_all(entries)
        try:
            async with sqlite_writer_section():
                await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        return entries

    async def aggregate_since(
        self,
        since: datetime,
        window: str | None = None,
    ) -> list[UsageAggregateRow]:
        conditions = [UsageHistory.recorded_at >= since]
        if window:
            conditions.append(_window_clause(window))
        stmt = (
            select(
                UsageHistory.account_id,
                func.avg(UsageHistory.used_percent).label("used_percent_avg"),
                func.sum(UsageHistory.input_tokens).label("input_tokens_sum"),
                func.sum(UsageHistory.output_tokens).label("output_tokens_sum"),
                func.count(UsageHistory.id).label("samples"),
                func.max(UsageHistory.recorded_at).label("last_recorded_at"),
                func.max(UsageHistory.reset_at).label("reset_at_max"),
                func.max(UsageHistory.window_minutes).label("window_minutes_max"),
            )
            .where(*conditions)
            .group_by(UsageHistory.account_id)
        )
        result = await self._session.execute(stmt)
        rows = result.all()
        return [
            UsageAggregateRow(
                account_id=row.account_id,
                used_percent_avg=float(row.used_percent_avg) if row.used_percent_avg is not None else None,
                input_tokens_sum=int(row.input_tokens_sum) if row.input_tokens_sum is not None else None,
                output_tokens_sum=int(row.output_tokens_sum) if row.output_tokens_sum is not None else None,
                samples=int(row.samples),
                last_recorded_at=row.last_recorded_at,
                reset_at_max=int(row.reset_at_max) if row.reset_at_max is not None else None,
                window_minutes_max=int(row.window_minutes_max) if row.window_minutes_max is not None else None,
            )
            for row in rows
        ]

    async def latest_by_account(
        self,
        window: str | None = None,
        *,
        account_ids: Collection[str] | None = None,
    ) -> dict[str, UsageHistory]:
        conditions = _window_clause(window)
        if account_ids is not None and not account_ids:
            return {}
        if account_ids is not None:
            conditions = and_(conditions, UsageHistory.account_id.in_(account_ids))
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        sqlite_path = _sqlite_path_from_bind(bind) if dialect == "sqlite" else None
        if sqlite_path is not None:
            return await to_thread.run_sync(
                _latest_by_account_sqlite,
                str(sqlite_path),
                window,
                list(account_ids) if account_ids is not None else None,
            )
        if dialect == "postgresql":
            acct_stmt = select(Account.id)
            if account_ids is not None:
                acct_stmt = acct_stmt.where(Account.id.in_(account_ids))
            acct_subq = acct_stmt.subquery("accts")
            lateral = (
                select(UsageHistory.id)
                .where(
                    conditions,
                    UsageHistory.account_id == acct_subq.c.id,
                )
                .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
                .limit(1)
                .correlate(acct_subq)
                .lateral("latest")
            )
            id_query = (
                select(lateral.c.id).select_from(acct_subq.outerjoin(lateral, true())).where(lateral.c.id.is_not(None))
            )
            stmt = select(UsageHistory).where(UsageHistory.id.in_(id_query))
            result = await self._session.execute(stmt)
            return {entry.account_id: entry for entry in result.scalars().all()}

        acct_stmt = select(Account.id)
        if account_ids is not None:
            acct_stmt = acct_stmt.where(Account.id.in_(account_ids))
        acct_subq = acct_stmt.subquery("accts")
        latest_id = (
            select(UsageHistory.id)
            .where(
                conditions,
                UsageHistory.account_id == acct_subq.c.id,
            )
            .order_by(UsageHistory.recorded_at.desc(), UsageHistory.id.desc())
            .limit(1)
            .correlate(acct_subq)
            .scalar_subquery()
        )
        id_rows = select(latest_id.label("usage_id")).select_from(acct_subq).subquery("latest_ids")
        stmt = select(UsageHistory).join(id_rows, UsageHistory.id == id_rows.c.usage_id)
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def history_since(
        self,
        account_id: str,
        window: str,
        since: datetime,
    ) -> list[UsageHistory]:
        stmt = (
            select(UsageHistory)
            .where(
                UsageHistory.account_id == account_id,
                _window_clause(window),
                UsageHistory.recorded_at >= since,
            )
            .order_by(UsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def bulk_history_since(
        self,
        account_ids: list[str],
        window: str,
        since: datetime,
    ) -> dict[str, list[UsageHistorySnapshot]]:
        """Fetch minimal usage history fields for multiple accounts in a single query."""
        if not account_ids:
            return {}
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        sqlite_path = _sqlite_path_from_bind(bind) if dialect == "sqlite" else None
        if sqlite_path is not None:
            return await to_thread.run_sync(
                _bulk_history_since_sqlite,
                str(sqlite_path),
                list(account_ids),
                window,
                since,
            )

        stmt = (
            select(
                UsageHistory.id,
                UsageHistory.account_id,
                UsageHistory.used_percent,
                UsageHistory.recorded_at,
                UsageHistory.reset_at,
                UsageHistory.window_minutes,
            )
            .where(
                UsageHistory.account_id.in_(account_ids),
                _window_clause(window),
                UsageHistory.recorded_at >= since,
            )
            .order_by(UsageHistory.account_id, UsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        grouped: dict[str, list[UsageHistorySnapshot]] = {}
        for row in result.all():
            snapshot = UsageHistorySnapshot(
                id=int(row.id),
                account_id=row.account_id,
                used_percent=float(row.used_percent),
                recorded_at=row.recorded_at,
                reset_at=float(row.reset_at) if row.reset_at is not None else None,
                window_minutes=int(row.window_minutes) if row.window_minutes is not None else None,
            )
            grouped.setdefault(snapshot.account_id, []).append(snapshot)
        return grouped

    async def trends_by_bucket(
        self,
        since: datetime,
        bucket_seconds: int = 21600,
        window: str | None = None,
        account_id: str | None = None,
    ) -> list[UsageTrendBucket]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "postgresql":
            bucket_expr = func.floor(func.extract("epoch", UsageHistory.recorded_at) / bucket_seconds) * bucket_seconds
        else:
            epoch_col = sqlalchemy_cast(func.strftime("%s", UsageHistory.recorded_at), Integer)
            bucket_expr = sqlalchemy_cast(epoch_col / bucket_seconds, Integer) * bucket_seconds
        bucket_col = bucket_expr.label("bucket_epoch")

        conditions: list = [UsageHistory.recorded_at >= since]
        if window:
            conditions.append(_window_clause(window))
        if account_id:
            conditions.append(UsageHistory.account_id == account_id)

        window_expr = _normalized_window_expr()
        if dialect == "sqlite":
            base_rows = (
                select(
                    bucket_col,
                    UsageHistory.id.label("usage_id"),
                    UsageHistory.account_id.label("account_id"),
                    window_expr.label("window"),
                    UsageHistory.used_percent.label("used_percent"),
                    UsageHistory.recorded_at.label("recorded_at"),
                )
                .where(*conditions)
                .subquery()
            )

            aggregate_rows = (
                select(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                    func.avg(base_rows.c.used_percent).label("avg_used_percent"),
                    func.count(base_rows.c.usage_id).label("samples"),
                    func.max(base_rows.c.recorded_at).label("max_recorded_at"),
                )
                .group_by(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                )
                .subquery()
            )

            latest_ids = (
                select(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                    func.max(base_rows.c.usage_id).label("usage_id"),
                )
                .join(
                    base_rows,
                    and_(
                        base_rows.c.bucket_epoch == aggregate_rows.c.bucket_epoch,
                        base_rows.c.account_id == aggregate_rows.c.account_id,
                        base_rows.c.window == aggregate_rows.c.window,
                        base_rows.c.recorded_at == aggregate_rows.c.max_recorded_at,
                    ),
                )
                .group_by(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                )
                .subquery()
            )

            stmt = (
                select(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                    aggregate_rows.c.avg_used_percent,
                    aggregate_rows.c.samples,
                    UsageHistory.reset_at,
                    UsageHistory.window_minutes,
                    UsageHistory.recorded_at,
                )
                .join(
                    latest_ids,
                    and_(
                        latest_ids.c.bucket_epoch == aggregate_rows.c.bucket_epoch,
                        latest_ids.c.account_id == aggregate_rows.c.account_id,
                        latest_ids.c.window == aggregate_rows.c.window,
                    ),
                )
                .join(UsageHistory, UsageHistory.id == latest_ids.c.usage_id)
                .order_by(aggregate_rows.c.bucket_epoch)
            )
        else:
            base_rows = (
                select(
                    bucket_col,
                    UsageHistory.id.label("usage_id"),
                    UsageHistory.account_id.label("account_id"),
                    window_expr.label("window"),
                    UsageHistory.used_percent.label("used_percent"),
                    UsageHistory.reset_at.label("reset_at"),
                    UsageHistory.window_minutes.label("window_minutes"),
                    UsageHistory.recorded_at.label("recorded_at"),
                )
                .where(*conditions)
                .subquery()
            )

            aggregate_rows = (
                select(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                    func.avg(base_rows.c.used_percent).label("avg_used_percent"),
                    func.count(base_rows.c.usage_id).label("samples"),
                )
                .group_by(
                    base_rows.c.bucket_epoch,
                    base_rows.c.account_id,
                    base_rows.c.window,
                )
                .subquery()
            )

            latest_rows = select(
                base_rows.c.bucket_epoch,
                base_rows.c.account_id,
                base_rows.c.window,
                base_rows.c.reset_at,
                base_rows.c.window_minutes,
                base_rows.c.recorded_at,
                func.row_number()
                .over(
                    partition_by=(base_rows.c.bucket_epoch, base_rows.c.account_id, base_rows.c.window),
                    order_by=(base_rows.c.recorded_at.desc(), base_rows.c.usage_id.desc()),
                )
                .label("row_number"),
            ).subquery()

            stmt = (
                select(
                    aggregate_rows.c.bucket_epoch,
                    aggregate_rows.c.account_id,
                    aggregate_rows.c.window,
                    aggregate_rows.c.avg_used_percent,
                    aggregate_rows.c.samples,
                    latest_rows.c.reset_at,
                    latest_rows.c.window_minutes,
                    latest_rows.c.recorded_at,
                )
                .join(
                    latest_rows,
                    and_(
                        latest_rows.c.bucket_epoch == aggregate_rows.c.bucket_epoch,
                        latest_rows.c.account_id == aggregate_rows.c.account_id,
                        latest_rows.c.window == aggregate_rows.c.window,
                        latest_rows.c.row_number == 1,
                    ),
                )
                .order_by(aggregate_rows.c.bucket_epoch)
            )

        result = await self._session.execute(stmt)
        return [
            UsageTrendBucket(
                bucket_epoch=int(row.bucket_epoch),
                account_id=row.account_id,
                window=row.window,
                avg_used_percent=float(row.avg_used_percent) if row.avg_used_percent is not None else 0.0,
                samples=int(row.samples),
                reset_at=int(row.reset_at) if row.reset_at is not None else None,
                window_minutes=int(row.window_minutes) if row.window_minutes is not None else None,
                recorded_at=row.recorded_at,
            )
            for row in result.all()
        ]

    async def latest_window_minutes(self, window: str) -> int | None:
        conditions = _window_clause(window)
        result = await self._session.execute(select(func.max(UsageHistory.window_minutes)).where(conditions))
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None


class AdditionalUsageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_entry(
        self,
        account_id: str,
        limit_name: str,
        metered_feature: str,
        window: str,
        used_percent: float,
        reset_at: int | None = None,
        window_minutes: int | None = None,
        recorded_at: datetime | None = None,
        quota_key: str | None = None,
    ) -> None:
        effective_quota_key = _resolve_additional_quota_key(
            quota_key=quota_key,
            limit_name=limit_name,
            metered_feature=metered_feature,
        )
        if effective_quota_key is None:
            raise ValueError("additional usage quota_key could not be determined")
        entry = AdditionalUsageHistory(
            account_id=account_id,
            quota_key=effective_quota_key,
            limit_name=limit_name,
            metered_feature=metered_feature,
            window=window,
            used_percent=used_percent,
            reset_at=reset_at,
            window_minutes=window_minutes,
            recorded_at=recorded_at or utcnow(),
        )
        self._session.add(entry)
        async with sqlite_writer_section():
            await self._session.commit()

    async def delete_for_account(self, account_id: str) -> None:
        stmt = delete(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == account_id)
        async with sqlite_writer_section():
            await self._session.execute(stmt)
            await self._session.commit()

    async def delete_for_account_and_quota_key(self, account_id: str, quota_key: str) -> None:
        scope = _resolve_additional_quota_query_scope(quota_key=quota_key)
        if scope is None:
            raise ValueError("additional usage quota_key could not be determined")
        stmt = delete(AdditionalUsageHistory).where(
            AdditionalUsageHistory.account_id == account_id,
            _additional_quota_match_clause(scope),
        )
        async with sqlite_writer_section():
            await self._session.execute(stmt)
            await self._session.commit()

    async def delete_for_account_and_limit(self, account_id: str, limit_name: str) -> None:
        await self.delete_for_account_and_quota_key(account_id, limit_name)

    async def delete_for_account_quota_key_window(
        self,
        account_id: str,
        quota_key: str,
        window: str,
    ) -> None:
        scope = _resolve_additional_quota_query_scope(quota_key=quota_key)
        if scope is None:
            raise ValueError("additional usage quota_key could not be determined")
        stmt = delete(AdditionalUsageHistory).where(
            AdditionalUsageHistory.account_id == account_id,
            _additional_quota_match_clause(scope),
            AdditionalUsageHistory.window == window,
        )
        async with sqlite_writer_section():
            await self._session.execute(stmt)
            await self._session.commit()

    async def delete_for_account_limit_window(
        self,
        account_id: str,
        limit_name: str,
        window: str,
    ) -> None:
        await self.delete_for_account_quota_key_window(account_id, limit_name, window)

    async def latest_by_account(
        self,
        quota_key: str | None = None,
        window: str | None = None,
        *,
        limit_name: str | None = None,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        """Returns the latest effective entry per account for a canonical quota key + window."""
        scope = _resolve_additional_quota_query_scope(
            quota_key=quota_key,
            limit_name=limit_name,
        )
        if scope is None or window is None:
            raise ValueError("quota_key/limit_name and window are required")
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        canonical_only = dialect == "postgresql"
        conditions = [
            _additional_quota_match_clause(scope, canonical_only=canonical_only),
            AdditionalUsageHistory.window == window,
        ]
        if account_ids is not None:
            account_ids = list(account_ids)
            if not account_ids:
                return {}
            conditions.append(AdditionalUsageHistory.account_id.in_(account_ids))
        if since is not None:
            conditions.append(AdditionalUsageHistory.recorded_at >= since)
        if dialect == "postgresql":
            latest_rows = (
                select(AdditionalUsageHistory)
                .where(*conditions)
                .distinct(AdditionalUsageHistory.account_id)
                .order_by(
                    AdditionalUsageHistory.account_id.asc(),
                    AdditionalUsageHistory.recorded_at.desc(),
                    AdditionalUsageHistory.used_percent.desc(),
                    AdditionalUsageHistory.id.desc(),
                )
            )
            result = await self._session.execute(latest_rows)
            entries = {entry.account_id: entry for entry in result.scalars().all()}
            alias_clause = _additional_quota_alias_match_clause(scope)
            if alias_clause is not None:
                alias_conditions = [
                    alias_clause,
                    AdditionalUsageHistory.window == window,
                ]
                if account_ids is not None:
                    alias_conditions.append(AdditionalUsageHistory.account_id.in_(account_ids))
                if since is not None:
                    alias_conditions.append(AdditionalUsageHistory.recorded_at >= since)
                alias_rows = (
                    select(AdditionalUsageHistory)
                    .where(*alias_conditions)
                    .distinct(AdditionalUsageHistory.account_id)
                    .order_by(
                        AdditionalUsageHistory.account_id.asc(),
                        AdditionalUsageHistory.recorded_at.desc(),
                        AdditionalUsageHistory.used_percent.desc(),
                        AdditionalUsageHistory.id.desc(),
                    )
                )
                alias_result = await self._session.execute(alias_rows)
                _merge_latest_additional_usage_entries(entries, alias_result.scalars().all())
            return entries

        if dialect == "sqlite":
            return await self._latest_by_scope_sqlite_probes(
                scope,
                window,
                account_ids=account_ids,
                since=since,
            )

        subq = (
            select(
                AdditionalUsageHistory.id.label("usage_id"),
                func.row_number()
                .over(
                    partition_by=AdditionalUsageHistory.account_id,
                    order_by=(
                        AdditionalUsageHistory.recorded_at.desc(),
                        AdditionalUsageHistory.used_percent.desc(),
                        AdditionalUsageHistory.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .where(*conditions)
            .subquery()
        )
        stmt = (
            select(AdditionalUsageHistory)
            .join(subq, AdditionalUsageHistory.id == subq.c.usage_id)
            .where(subq.c.row_number == 1)
        )
        result = await self._session.execute(stmt)
        return {entry.account_id: entry for entry in result.scalars().all()}

    async def _latest_by_scope_sqlite_probes(
        self,
        scope: AdditionalQuotaQueryScope,
        window: str,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        account_values = list(account_ids) if account_ids is not None else None
        if account_values is not None and not account_values:
            return {}
        account_stmt = select(AdditionalUsageHistory.account_id).where(
            _additional_quota_match_clause(scope),
            AdditionalUsageHistory.window == window,
        )
        if account_values is not None:
            account_stmt = account_stmt.where(AdditionalUsageHistory.account_id.in_(account_values))
        if since is not None:
            account_stmt = account_stmt.where(AdditionalUsageHistory.recorded_at >= since)
        account_result = await self._session.execute(account_stmt.distinct())
        latest: dict[str, AdditionalUsageHistory] = {}
        for account_id in account_result.scalars().all():
            latest_stmt = (
                select(AdditionalUsageHistory)
                .where(
                    AdditionalUsageHistory.account_id == account_id,
                    _additional_quota_match_clause(scope),
                    AdditionalUsageHistory.window == window,
                )
                .order_by(
                    AdditionalUsageHistory.recorded_at.desc(),
                    AdditionalUsageHistory.used_percent.desc(),
                    AdditionalUsageHistory.id.desc(),
                )
                .limit(1)
            )
            if since is not None:
                latest_stmt = latest_stmt.where(AdditionalUsageHistory.recorded_at >= since)
            row_result = await self._session.execute(latest_stmt)
            entry = row_result.scalar_one_or_none()
            if entry is not None:
                latest[entry.account_id] = entry
        return latest

    async def latest_by_quota_key(
        self,
        quota_key: str,
        window: str,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> dict[str, AdditionalUsageHistory]:
        bind = self._session.get_bind()
        dialect = bind.dialect.name if bind else "sqlite"
        if dialect == "sqlite":
            scope = _resolve_additional_quota_query_scope(quota_key=quota_key)
            if scope is None:
                raise ValueError("quota_key and window are required")
            return await self._latest_by_scope_sqlite_probes(
                scope,
                window,
                account_ids=account_ids,
                since=since,
            )
        return await self.latest_by_account(
            quota_key=quota_key,
            window=window,
            account_ids=account_ids,
            since=since,
        )

    async def list_quota_keys(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        stmt = select(
            AdditionalUsageHistory.quota_key,
            AdditionalUsageHistory.limit_name,
            AdditionalUsageHistory.metered_feature,
        ).distinct()
        if account_ids is not None:
            stmt = stmt.where(AdditionalUsageHistory.account_id.in_(account_ids))
        if since is not None:
            stmt = stmt.where(AdditionalUsageHistory.recorded_at >= since)
        result = await self._session.execute(stmt)
        resolved_keys = {
            resolved_key
            for quota_key_value, limit_name_value, metered_feature_value in result.all()
            if (
                resolved_key := canonicalize_additional_quota_key(
                    quota_key=quota_key_value,
                    limit_name=limit_name_value,
                    metered_feature=metered_feature_value,
                )
            )
            is not None
        }
        return sorted(resolved_keys)

    async def list_limit_names(
        self,
        *,
        account_ids: Collection[str] | None = None,
        since: datetime | None = None,
    ) -> list[str]:
        return await self.list_quota_keys(account_ids=account_ids, since=since)

    async def history_since(
        self,
        account_id: str,
        quota_key: str | None = None,
        window: str | None = None,
        since: datetime | None = None,
        *,
        limit_name: str | None = None,
    ) -> list[AdditionalUsageHistory]:
        """Returns time-series entries for EWMA computation."""
        scope = _resolve_additional_quota_query_scope(
            quota_key=quota_key,
            limit_name=limit_name,
        )
        if scope is None or window is None or since is None:
            raise ValueError("account_id, quota_key/limit_name, window, and since are required")
        stmt = (
            select(AdditionalUsageHistory)
            .where(
                AdditionalUsageHistory.account_id == account_id,
                _additional_quota_match_clause(scope),
                AdditionalUsageHistory.window == window,
                AdditionalUsageHistory.recorded_at >= since,
            )
            .order_by(AdditionalUsageHistory.recorded_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def latest_recorded_at_for_account(self, account_id: str) -> datetime | None:
        """Return the most recent recorded_at for any additional usage entry of this account."""
        stmt = select(func.max(AdditionalUsageHistory.recorded_at)).where(
            AdditionalUsageHistory.account_id == account_id
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def latest_recorded_at(self) -> datetime | None:
        """Return the most recent recorded_at across all additional usage entries."""
        stmt = select(func.max(AdditionalUsageHistory.recorded_at))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()
