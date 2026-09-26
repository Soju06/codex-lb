"""Dialect-aware SQL fragments for the MySQL port (side branch).

The application was written for SQLite and PostgreSQL, which both support the
``count(...) FILTER (WHERE ...)`` aggregate clause. MySQL does not; the
equivalent there is ``count(CASE WHEN ... THEN ... END)``. These helpers pick
the right form at query-build time so each dialect keeps its native SQL.
"""

from __future__ import annotations

import inspect
from typing import Any, Mapping, Sequence

from sqlalchemy import ColumnElement, Integer, String, case, func, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

MYSQL_DIALECT_NAMES = frozenset({"mysql", "mariadb"})

#: Byte-exact collation for the sentinel-encoded dimension comparisons.
MYSQL_BINARY_COLLATION = "utf8mb4_bin"


def _dialect_name_of(target: Any) -> str:
    """Dialect name behind a session, connection, engine/bind or name string."""

    if isinstance(target, str):
        return target
    dialect = getattr(target, "dialect", None)
    if dialect is not None:  # Connection / Engine / inspector-style object
        return str(getattr(dialect, "name", ""))
    return dialect_name(target)


def is_mysql(target: Any) -> bool:
    """True when the dialect behind a session/connection/engine/name is MySQL.

    One predicate for the whole app: call sites ask this instead of re-spelling
    the dialect tuple in every branch, so the set of MySQL dialect names -- and
    any future decision about how to branch on them -- lives in exactly one
    place. Prefer a capability helper from this module (``dialect_upsert``,
    ``delete_returning``, ``statement_matched``, ``same_table_subquery``,
    ``epoch_seconds``) over branching on this directly: a call site that reads as
    *what* it needs is the one that stays correct when a fourth backend arrives.
    """

    return _dialect_name_of(target) in MYSQL_DIALECT_NAMES


def is_mariadb(target: Any) -> bool:
    """True when the dialect behind a session/connection/engine/name is MariaDB.

    A ``mysql+pymysql://`` (or ``mysql+asyncmy://``) URL keeps the dialect name
    ``mysql`` even when the server is MariaDB, so the name alone is not enough:
    SQLAlchemy reports the server through the dialect's ``is_mariadb`` flag.
    """
    dialect = getattr(target, "dialect", target)
    if str(getattr(dialect, "name", "")) == "mariadb":
        return True
    return bool(getattr(dialect, "is_mariadb", False))


def dialect_name(session: Any) -> str:
    """Name of the dialect behind a Session/AsyncSession ('' when unknown)."""
    try:
        bind = session.get_bind()
    except Exception:  # pragma: no cover - defensive, dialect detection only
        return ""
    if inspect.iscoroutine(bind):  # mocked/async get_bind: treat as unknown
        bind.close()
        return ""
    dialect = getattr(bind, "dialect", None)
    return str(getattr(dialect, "name", "")) if dialect is not None else ""


def same_table_subquery(select_stmt: Any, column: Any, *, dialect: str) -> Any:
    """A batch ``SELECT`` usable inside a same-table ``UPDATE``/``DELETE``.

    MySQL refuses statements whose subquery reads the statement's own target
    table (error 1093, "You can't specify target table ... for update in FROM
    clause"). Wrapping the batch select in a derived table materialises it and
    keeps the statement legal there; every other backend keeps the direct
    subquery it has always used.
    """
    if dialect in MYSQL_DIALECT_NAMES:
        derived = select_stmt.subquery()
        return select(derived.c[column.name]).scalar_subquery()
    return select_stmt.scalar_subquery()


def dialect_upsert(
    session: Any,
    table: Any,
    values: dict[str, Any],
    *,
    conflict_columns: Sequence[str],
    update_columns: Mapping[str, Any] | None = None,
) -> Any:
    """Build an upsert with each dialect's native syntax.

    PostgreSQL and SQLite use ``ON CONFLICT`` (``DO UPDATE``/``DO NOTHING``);
    MySQL/MariaDB use ``ON DUPLICATE KEY UPDATE``, which targets the table's
    unique/primary keys directly. ``update_columns`` maps column names to the
    new value expressions; pass ``None`` for "do nothing" semantics (``id`` is
    rewritten to its inserted value, a no-op update on MySQL).
    """
    dialect = dialect_name(session)
    if dialect in MYSQL_DIALECT_NAMES:
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        statement = mysql_insert(table).values(**values)
        if update_columns:
            return statement.on_duplicate_key_update(**dict(update_columns))
        first_column = conflict_columns[0]
        return statement.on_duplicate_key_update(**{first_column: getattr(statement.inserted, first_column)})

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    statement = insert_fn(table).values(**values)
    if update_columns:
        return statement.on_conflict_do_update(index_elements=list(conflict_columns), set_=dict(update_columns))
    return statement.on_conflict_do_nothing(index_elements=list(conflict_columns))


def conditional_count(
    session: Any,
    expression: ColumnElement[Any],
    condition: ColumnElement[bool],
) -> ColumnElement[int]:
    """``count(expression) FILTER (WHERE condition)`` for every dialect.

    PostgreSQL and SQLite get the native ``FILTER`` clause; MySQL/MariaDB get
    ``count(CASE WHEN condition THEN expression END)``, which has identical
    semantics (NULL rows from the CASE are not counted).
    """
    if dialect_name(session) in MYSQL_DIALECT_NAMES:
        return func.count(case((condition, expression)))
    return func.count(expression).filter(condition)


async def delete_returning(session: Any, statement: Any, *columns: Any) -> list[Any]:
    """``DELETE … RETURNING columns`` for every dialect.

    MySQL has no ``DELETE … RETURNING``: the rows the delete would touch are
    selected first under ``FOR UPDATE`` (inside the caller's transaction, so
    they cannot be stolen meanwhile) and deleted second; the caller receives
    the same rows the RETURNING form would have produced.
    """
    if dialect_name(session) not in MYSQL_DIALECT_NAMES:
        result = await session.execute(statement.returning(*columns))
        return list(result.all())

    from sqlalchemy import select

    probe = select(*columns).with_for_update()
    whereclause = getattr(statement, "whereclause", None)
    if whereclause is not None:
        probe = probe.where(whereclause)
    rows = list((await session.execute(probe)).all())
    if rows:
        await session.execute(statement)
    return rows


async def update_returning(session: Any, statement: Any, *columns: Any) -> list[Any]:
    """``UPDATE … RETURNING columns`` for every dialect.

    MySQL has no ``UPDATE … RETURNING`` either: the matching rows are read
    first under ``FOR UPDATE`` and the update runs second. That is faithful for
    the returning columns this codebase uses (row identities: ``key``, ``id``);
    a caller that needs the *updated* value of a changed column must re-select
    after the update instead.
    """
    if dialect_name(session) not in MYSQL_DIALECT_NAMES:
        result = await session.execute(statement.returning(*columns))
        return list(result.all())

    from sqlalchemy import select

    probe = select(*columns).with_for_update()
    whereclause = getattr(statement, "whereclause", None)
    if whereclause is not None:
        probe = probe.where(whereclause)
    rows = list((await session.execute(probe)).all())
    if rows:
        await session.execute(statement)
    return rows


async def statement_matched(session: Any, statement: Any, identity_column: Any) -> bool:
    """Verdict of a guarded UPDATE/DELETE: did it match a row?

    PostgreSQL and SQLite report the matched row identity through
    ``RETURNING`` (a matched row counts even when the rewrite stores identical
    values — the contract the repository unit tests pin). MySQL has no
    ``RETURNING``; its affected-row count is the verdict, read from the
    guarded statement itself.
    """
    if dialect_name(session) in MYSQL_DIALECT_NAMES:
        result = await session.execute(statement)
        return (result.rowcount or 0) > 0
    row = (await session.execute(statement.returning(identity_column))).scalar_one_or_none()
    return row is not None


class epoch_seconds(FunctionElement):
    """Epoch seconds of a datetime expression, per dialect.

    SQLite has ``strftime('%s', col)``, PostgreSQL ``extract(epoch from col)``
    and MySQL ``unix_timestamp(col)``; this element renders the right one at
    compile time so call sites stay dialect-neutral.
    """

    type = Integer()
    name = "epoch_seconds"
    inherit_cache = True


def _epoch_argument(element: FunctionElement) -> Any:
    return list(element.clauses)[0]


@compiles(epoch_seconds, "mysql")
@compiles(epoch_seconds, "mariadb")
def _compile_epoch_seconds_mysql(element, compiler, **kw):  # type: ignore[no-untyped-def]
    # ``UNIX_TIMESTAMP(col)`` converts the stored naive datetime *from the session
    # time zone*, so the same row buckets differently on a server or connection
    # whose zone is not UTC. The database stores naive UTC, so take the arithmetic
    # difference from the epoch instead: two DATETIME values, no zone involved.
    argument = compiler.process(_epoch_argument(element), **kw)
    return f"CAST(TIMESTAMPDIFF(SECOND, '1970-01-01 00:00:00', {argument}) AS SIGNED)"


@compiles(epoch_seconds, "postgresql")
def _compile_epoch_seconds_postgresql(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"CAST(EXTRACT(EPOCH FROM {compiler.process(_epoch_argument(element), **kw)}) AS BIGINT)"


@compiles(epoch_seconds)
def _compile_epoch_seconds_default(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"CAST(strftime('%s', {compiler.process(_epoch_argument(element), **kw)}) AS INTEGER)"


class whitespace_trim(FunctionElement):
    """Trim leading/trailing whitespace, dialect by dialect.

    SQLite and PostgreSQL expose two-argument ``ltrim``/``rtrim``; MySQL's
    LTRIM/RTRIM take a single argument, so MySQL gets an anchored
    ``REGEXP_REPLACE`` over the whitespace class instead.
    """

    type = String()
    name = "whitespace_trim"
    inherit_cache = False

    def __init__(self, column: Any, characters: str) -> None:
        super().__init__(column)
        self.characters = characters


def _whitespace_sql_literal(characters: str) -> str:
    return "'" + characters.replace("'", "''") + "'"


class binary_collated(FunctionElement):
    """Force a byte-exact collation on MySQL/MariaDB; no-op elsewhere.

    SQLite and PostgreSQL compare text byte-exactly by default. MySQL's
    ``utf8mb4_0900_ai_ci`` treats U+001F — the dimension sentinel — as an
    ignorable character, so sentinel comparisons, ``GROUP BY`` results and
    unique keys must be forced to the binary collation there for the encoding
    to stay injective.
    """

    type = String()
    name = "binary_collated"
    inherit_cache = False


@compiles(binary_collated, "mysql")
@compiles(binary_collated, "mariadb")
def _compile_binary_collated_mysql(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"({compiler.process(list(element.clauses)[0], **kw)} COLLATE {MYSQL_BINARY_COLLATION})"


@compiles(binary_collated)
def _compile_binary_collated_default(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return compiler.process(list(element.clauses)[0], **kw)


@compiles(whitespace_trim, "mysql")
@compiles(whitespace_trim, "mariadb")
def _compile_whitespace_trim_mysql(element, compiler, **kw):  # type: ignore[no-untyped-def]
    argument = compiler.process(list(element.clauses)[0], **kw)
    return f"REGEXP_REPLACE({argument}, '^[[:space:]]+|[[:space:]]+$', '')"


@compiles(whitespace_trim)
def _compile_whitespace_trim_default(element, compiler, **kw):  # type: ignore[no-untyped-def]
    argument = compiler.process(list(element.clauses)[0], **kw)
    literal = _whitespace_sql_literal(element.characters)
    return f"ltrim(rtrim({argument}, {literal}), {literal})"


class greatest(FunctionElement):
    """Scalar maximum of two or more arguments.

    SQLite spells it ``max(a, b)``; PostgreSQL and MySQL spell it ``GREATEST``.
    ``func.max(a, b)`` compiles to ``max(a, b)`` on MySQL, which is an aggregate
    and fails — this element renders each dialect's scalar form instead.
    """

    inherit_cache = True
    name = "greatest"

    @property
    def type(self):  # type: ignore[override]
        return list(self.clauses)[0].type


class least(FunctionElement):
    """Scalar minimum of two or more arguments (``min`` / ``LEAST``)."""

    inherit_cache = True
    name = "least"

    @property
    def type(self):  # type: ignore[override]
        return list(self.clauses)[0].type


def _render_arguments(element: object, compiler: object, kw: dict) -> str:  # type: ignore[no-untyped-def]
    return ", ".join(compiler.process(clause, **kw) for clause in element.clauses)  # type: ignore[attr-defined]


@compiles(greatest, "mysql")
@compiles(greatest, "mariadb")
@compiles(greatest, "postgresql")
def _compile_greatest_std(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"GREATEST({_render_arguments(element, compiler, kw)})"


@compiles(least, "mysql")
@compiles(least, "mariadb")
@compiles(least, "postgresql")
def _compile_least_std(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"LEAST({_render_arguments(element, compiler, kw)})"


@compiles(greatest)
def _compile_greatest_sqlite(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"max({_render_arguments(element, compiler, kw)})"


@compiles(least)
def _compile_least_sqlite(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return f"min({_render_arguments(element, compiler, kw)})"
