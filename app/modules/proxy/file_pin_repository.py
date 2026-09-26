from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import Integer, bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from app.db.dialect_sql import is_mysql
from app.db.session import sqlite_writer_section

_TABLE = "file_account_pins"


# Ownership TTLs stay entirely in the database clock domain, and every backend
# spells its clock differently. One spec per dialect keeps the difference in a
# single visible place: the statements below are written once and rendered with
# the clock of whichever backend is asking.
#
# * PostgreSQL's ``clock_timestamp()`` is evaluated when each clause executes;
#   ``statement_timestamp()`` (the delete sweep) is fixed for the statement.
# * SQLite's padded ``strftime`` form matches SQLAlchemy ``DateTime``'s
#   six-digit fractional width, preserving exact lexicographic expiry checks.
# * MySQL's ``NOW(6)`` matches the microsecond width the port's ``DATETIME(6)``
#   columns keep, so its expiry comparisons stay as precise as the others'.
_SQLITE_NOW = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
_SQLITE_NOW_PLUS_TTL = "(strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds') || '000')"


@dataclass(frozen=True)
class _Clock:
    """How one backend spells "now" and "now plus the TTL"."""

    now: str
    sweep_now: str
    now_plus_ttl: str


_CLOCKS: dict[str, _Clock] = {
    "postgresql": _Clock(
        now="clock_timestamp()",
        sweep_now="statement_timestamp()",
        now_plus_ttl="clock_timestamp() + make_interval(secs => :ttl)",
    ),
    "sqlite": _Clock(now=_SQLITE_NOW, sweep_now=_SQLITE_NOW, now_plus_ttl=_SQLITE_NOW_PLUS_TTL),
    "mysql": _Clock(now="NOW(6)", sweep_now="NOW(6)", now_plus_ttl="(NOW(6) + INTERVAL :ttl SECOND)"),
}

# A claim takes over an expired pin, extends a live pin of its own owner, and
# leaves a live pin belonging to someone else alone. PostgreSQL and SQLite say
# that with ``ON CONFLICT ... DO UPDATE ... WHERE`` and return the persisted
# owner.
_CLAIM_SQL = """
INSERT INTO {table} (file_id, account_id, expires_at)
VALUES (:file_id, :account_id, {now_plus_ttl})
ON CONFLICT (file_id) DO UPDATE SET
    account_id = excluded.account_id,
    expires_at = {now_plus_ttl}
WHERE {table}.account_id = :account_id
   OR {table}.expires_at <= {now}
RETURNING account_id
"""

# MySQL has neither ``ON CONFLICT`` nor ``RETURNING``: the conditional upsert
# runs as ``ON DUPLICATE KEY UPDATE`` and the callers read the persisted owner
# back in the same transaction (see ``FileAccountPinRepository.claim``). The
# assignment order matters -- ``account_id`` is settled first, from the
# pre-update expiry, and ``expires_at`` then reads the value that assignment
# produced: MySQL evaluates an ``ON DUPLICATE KEY UPDATE`` list left to right
# against the row as it stands, so this is "take over an expired pin, extend a
# live one of ours, leave a live pin of someone else's alone" expressed without
# ``VALUES()`` or a row alias (both keep MariaDB working).
_MYSQL_CLAIM_SQL = """
INSERT INTO {table} (file_id, account_id, expires_at)
VALUES (:file_id, :account_id, {now_plus_ttl})
ON DUPLICATE KEY UPDATE
    account_id = IF(
        {table}.account_id = :account_id OR {table}.expires_at <= {now},
        :account_id,
        {table}.account_id
    ),
    expires_at = IF(
        {table}.account_id = :account_id OR {table}.expires_at <= {now},
        {now_plus_ttl},
        {table}.expires_at
    )
"""

_SWEEP_SQL = "DELETE FROM {table} WHERE expires_at <= {sweep_now}"

_REFRESH_SQL = """
UPDATE {table}
SET expires_at = {now_plus_ttl}
WHERE file_id = :file_id
  AND account_id = :account_id
RETURNING account_id
"""
# MySQL cannot RETURNING the refreshed owner; the guarded update's row count is
# the verdict the caller needs, and the owner it may have raced is read back
# separately.
_MYSQL_REFRESH_SQL = """
UPDATE {table}
SET expires_at = {now_plus_ttl}
WHERE file_id = :file_id
  AND account_id = :account_id
"""

_GET_LIVE_SQL = "SELECT account_id FROM {table} WHERE file_id = :file_id AND expires_at > {now}"

_GET_LIVE_MANY_SQL = """
SELECT file_id, account_id
FROM {table}
WHERE file_id IN :file_ids
  AND expires_at > {now}
"""


def _render(template: str, clock: _Clock) -> TextClause:
    return text(
        template.format(
            table=_TABLE,
            now=clock.now,
            sweep_now=clock.sweep_now,
            now_plus_ttl=clock.now_plus_ttl,
        )
    )


def _rendered(template: str, *, ttl: bool = False, file_ids: bool = False) -> dict[str, TextClause]:
    """Render one template for every supported dialect, binding its placeholders."""

    statements: dict[str, TextClause] = {}
    for dialect, clock in _CLOCKS.items():
        statement = _render(template, clock)
        if ttl:
            statement = statement.bindparams(bindparam("ttl", type_=Integer))
        if file_ids:
            statement = statement.bindparams(bindparam("file_ids", expanding=True))
        statements[dialect] = statement
    return statements


_CLAIMS = {
    **_rendered(_CLAIM_SQL, ttl=True),
    # MySQL keeps its own shape, so it replaces the rendered ON CONFLICT form.
    "mysql": _render(_MYSQL_CLAIM_SQL, _CLOCKS["mysql"]).bindparams(bindparam("ttl", type_=Integer)),
}
_REFRESHES = _rendered(_REFRESH_SQL, ttl=True)
_REFRESHES["mysql"] = _render(_MYSQL_REFRESH_SQL, _CLOCKS["mysql"]).bindparams(bindparam("ttl", type_=Integer))
_GET_ACCOUNT = text(f"SELECT account_id FROM {_TABLE} WHERE file_id = :file_id")
_SWEEPS = _rendered(_SWEEP_SQL)
_LIVE_LOOKUPS = _rendered(_GET_LIVE_SQL)
_LIVE_MANY_LOOKUPS = _rendered(_GET_LIVE_MANY_SQL, file_ids=True)


def _for_dialect(statements: dict[str, TextClause], dialect_name: str) -> TextClause:
    key = "mysql" if is_mysql(dialect_name) else dialect_name
    try:
        return statements[key]
    except KeyError:
        raise RuntimeError(f"Unsupported database dialect for file account pins: {dialect_name}") from None


class FileAccountPinOwnershipConflict(RuntimeError):
    def __init__(self, file_id: str, persisted_account_id: str, requested_account_id: str) -> None:
        super().__init__(
            f"Live file ownership conflict for {file_id!r}: "
            f"persisted={persisted_account_id!r} requested={requested_account_id!r}"
        )
        self.file_id = file_id
        self.persisted_account_id = persisted_account_id
        self.requested_account_id = requested_account_id


def build_file_account_pin_claim(*, dialect_name: str) -> TextClause:
    return _for_dialect(_CLAIMS, dialect_name)


def build_file_account_pin_cleanup(*, dialect_name: str) -> TextClause:
    return _for_dialect(_SWEEPS, dialect_name)


def build_file_account_pin_refresh(*, dialect_name: str) -> TextClause:
    return _for_dialect(_REFRESHES, dialect_name)


def build_file_account_pin_live_lookup(*, dialect_name: str, many: bool = False) -> TextClause:
    return _for_dialect(_LIVE_MANY_LOOKUPS if many else _LIVE_LOOKUPS, dialect_name)


class FileAccountPinRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, file_id: str, account_id: str, *, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("File account pin TTL must be positive")
        dialect_name = self._dialect_name()
        params = {
            "file_id": file_id,
            "account_id": account_id,
            "ttl": ttl_seconds,
        }
        async with sqlite_writer_section():
            await self._session.execute(build_file_account_pin_cleanup(dialect_name=dialect_name))
            if is_mysql(dialect_name):
                # No ``RETURNING`` on the MySQL upsert: the conditional upsert
                # settles ownership, then the row is read back inside the same
                # transaction (which sees its own write), and the comparison
                # below produces the same verdict as the RETURNING forms.
                await self._session.execute(
                    build_file_account_pin_claim(dialect_name=dialect_name),
                    params,
                )
                persisted_account_id = await self._session.scalar(
                    _GET_ACCOUNT,
                    {"file_id": file_id},
                )
            else:
                persisted_account_id = (
                    await self._session.execute(
                        build_file_account_pin_claim(dialect_name=dialect_name),
                        params,
                    )
                ).scalar_one_or_none()
                if persisted_account_id is None:
                    persisted_account_id = await self._session.scalar(
                        _GET_ACCOUNT,
                        {"file_id": file_id},
                    )
            if persisted_account_id != account_id:
                await self._session.rollback()
                raise FileAccountPinOwnershipConflict(
                    file_id,
                    persisted_account_id or "<missing>",
                    account_id,
                )
            if is_mysql(dialect_name):
                # The refresh's verdict is its affected-row count: MySQL has no
                # ``RETURNING``, and the guarded UPDATE only rewrites the pin
                # the claim just took, so a matched row is a successful refresh.
                refreshed = await self._session.execute(
                    build_file_account_pin_refresh(dialect_name=dialect_name),
                    params,
                )
                refreshed_account_id = account_id if (refreshed.rowcount or 0) > 0 else None
            else:
                refreshed_account_id = (
                    await self._session.execute(
                        build_file_account_pin_refresh(dialect_name=dialect_name),
                        params,
                    )
                ).scalar_one_or_none()
            if refreshed_account_id != account_id:
                await self._session.rollback()
                raise RuntimeError(f"Failed to refresh file account pin after claim: {file_id!r}")
            await self._session.commit()

    async def get_live_account_id(self, file_id: str) -> str | None:
        return await self._session.scalar(
            build_file_account_pin_live_lookup(dialect_name=self._dialect_name()),
            {"file_id": file_id},
        )

    async def get_live_account_ids(self, file_ids: Collection[str]) -> dict[str, str]:
        unique_file_ids = tuple(dict.fromkeys(file_ids))
        if not unique_file_ids:
            return {}
        rows = (
            (
                await self._session.execute(
                    build_file_account_pin_live_lookup(
                        dialect_name=self._dialect_name(),
                        many=True,
                    ),
                    {"file_ids": unique_file_ids},
                )
            )
            .tuples()
            .all()
        )
        return dict(rows)

    def _dialect_name(self) -> str:
        return self._session.get_bind().dialect.name
