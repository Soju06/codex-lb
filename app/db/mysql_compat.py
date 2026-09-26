"""MySQL type compatibility for the ORM schema (side branch, MySQL port).

The schema was written for SQLite and PostgreSQL, where an unbounded
``String`` is portable and ``Text`` is unlimited. MySQL requires:

* ``VARCHAR`` to carry an explicit length, and
* ``TEXT`` is capped at 64 KiB (``MEDIUMTEXT`` is 16 MiB, ``LONGTEXT`` 4 GiB).

Measured maxima across the production snapshot (2026-09-21) put every
unbounded ``String`` column at or below 298 characters, except columns that
are already declared ``Text`` — ``model_registry_snapshot.payload`` reached
1,021,766 characters. The mapping below therefore:

* renders an unbounded ``String`` as ``VARCHAR(255)`` (indexable: 255 × 4
  bytes = 1020 B, inside MySQL's 3072-byte utf8mb4 key limit, including inside
  composite keys such as ``sticky_sessions(key, kind)``); columns that need
  more keep an explicit length in the models, and
* renders ``Text`` as ``MEDIUMTEXT`` so multi-megabyte payloads (model
  registry snapshots, long error traces) fit instead of failing at 64 KiB, and
* renders ``DateTime`` as ``DATETIME(6)``: MySQL's plain ``DATETIME`` carries no
  fractional seconds, so a Python-bound ``utcnow()`` (which has microseconds)
  came back rounded to the whole second -- sometimes into the next second --
  while SQLite and PostgreSQL round-trip the same value unchanged. Application
  code compares stored timestamps against Python-computed instants (limit
  windows, rollup buckets, lease deadlines), so the precision has to match.

This module only affects DDL compiled for the MySQL dialect; SQLite and
PostgreSQL output is unchanged.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    LargeBinary,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.mysql import VARBINARY, VARCHAR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DefaultClause

from app.db.dialect_sql import MYSQL_BINARY_COLLATION, MYSQL_DIALECT_NAMES, is_mysql

MYSQL_UNBOUNDED_STRING_LENGTH = 255
MYSQL_TEXT_TYPE = "MEDIUMTEXT"
MYSQL_KEYED_BINARY_LENGTH = 64

#: Column-name based lengths, applied to any unbounded ``String`` that reaches
#: MySQL DDL (models or migration-defined tables). Derived from the measured
#: maxima of the production snapshot plus the column's role:
#: ids <= 55 chars measured, keys/hashes <= 101, status/enum-like <= 32,
#: labels/names <= 96. Sizes stay small enough that composite index keys fit
#: MySQL's 3072-byte utf8mb4 limit with room to spare.
MYSQL_ENUM_LIKE_COLUMNS = frozenset(
    {
        "status",
        "kind",
        "type",
        "mode",
        "state",
        "window",
        "source",
        "tier",
        "level",
        "scope",
        "role",
        "origin",
        "provider",
        "plan",
        "currency",
        "unit",
        "channel",
        "region",
        "locale",
        "environment",
        "stage",
        "platform",
        "severity",
        "action",
        "result",
        "outcome",
        "strategy",
        "direction",
        "category",
        "event",
        "target",
        "operation",
        "service_tier",
        "reasoning_effort",
        "billing_mode",
        "quota_key",
    }
)


def mysql_string_length_for(column_name: str) -> int:
    """Pick a MySQL ``VARCHAR`` length for an unbounded ``String`` column.

    Lengths come from the measured maxima of the production snapshot
    (2026-09-21) plus the column's role. They are deliberately a couple of
    times the measured maximum so ordinary growth does not truncate, while
    composite index keys stay inside MySQL's 3072-byte utf8mb4 limit.
    """
    name = column_name.lower()
    if name == "key":
        return 128
    if name == "id" or name.endswith("_id"):
        return 96
    if name.endswith(("_key", "_hash", "_token", "_secret", "_fingerprint", "_etag", "_nonce", "_salt")):
        return 128
    if name in MYSQL_ENUM_LIKE_COLUMNS:
        return 32
    if name.endswith(
        (
            "_kind",
            "_type",
            "_state",
            "_status",
            "_mode",
            "_source",
            "_tier",
            "_level",
            "_scope",
            "_window",
            "_origin",
            "_stage",
            "_channel",
            "_region",
            "_locale",
            "_role",
            "_plan",
            "_provider",
            "_category",
            "_direction",
            "_outcome",
            "_action",
            "_result",
            "_event",
            "_target",
            "_operation",
        )
    ):
        return 32
    # ``*_code`` columns reach 37 characters in the production snapshot
    # (``error_code``), so they take the 64 bucket — never the enum set above.
    if name == "model" or name.endswith(("_name", "_feature", "_code", "_label", "_title", "_slug", "_alias")):
        return 64
    return MYSQL_UNBOUNDED_STRING_LENGTH


#: ``Text`` columns that are semantically short and appear in MySQL indexes.
#: MySQL cannot index TEXT/MEDIUMTEXT without a key prefix, so on MySQL these
#: become sized VARCHARs. Evidence: ``downstream_turn_state`` values are
#: ``turn_<32 hex>`` / ``http_turn_<32 hex>`` (<= 42 chars) and response ids are
#: ``resp_*`` (<= 70 chars); everything else that is genuinely long stays Text.
MYSQL_TEXT_AS_VARCHAR: dict[str, int] = {
    # response / turn identifiers: measured <= 70 chars, and indexed on MySQL
    "latest_response_id": 128,
    "latest_turn_state": 128,
    "parent_response_id": 128,
    "response_id": 128,
    # password hashes: measured <= 60 chars, unique-indexed on MySQL
    "password_hash": 255,
    "guest_password_hash": 255,
    # sticky session key value: measured <= 101 chars
    "session_key_value": 255,
}

#: ``LargeBinary`` columns that carry a key on MySQL (hashes, measured 32 bytes)
MYSQL_BINARY_AS_VARBINARY = frozenset({"token_hash", "bootstrap_token_hash", "key_hash"})

#: Rollup dimension columns store the ``DIMENSION_SENTINEL`` (U+001F) encoding
#: of nullable raw values. MySQL's default ``utf8mb4_0900_ai_ci`` collation
#: treats U+001F as an ignorable character, so the sentinel compares equal to
#: the empty string there — ``GROUP BY`` and unique keys would merge them and
#: the folded reports lose (or double-count) the empty dimension. These
#: columns therefore get a byte-exact collation on MySQL, matching the binary
#: comparison SQLite and PostgreSQL provide natively.
MYSQL_SENTINEL_COLUMNS: dict[str, frozenset[str]] = {
    # ``account_usage_rollups.account_id`` and ``api_key_usage_rollups.api_key_id``
    # are foreign keys to ``accounts.id`` / ``api_keys.id`` and must keep the
    # referenced column's collation; those tables only ever store real ids, so
    # the sentinel encoding never reaches them.
    "request_report_hourly_rollups": frozenset({"account_id", "api_key_id", "useragent_group", "conversation_id"}),
    "request_usage_hourly_rollups": frozenset({"account_id", "api_key_id", "service_tier"}),
    "request_usage_hourly_error_rollups": frozenset({"account_id"}),
    "request_demand_quarter_rollups": frozenset({"account_id", "api_key_id", "reasoning_effort"}),
    "request_conversation_hourly_rollups": frozenset({"account_id", "conversation_id"}),
}


def mysql_string_for(column_name: str) -> String:
    """A ``String`` whose MySQL/MariaDB DDL is ``VARCHAR(rule(name))``.

    The variant keeps SQLite and PostgreSQL on the original unbounded
    ``String`` while MySQL gets the size it requires, so model metadata,
    migration DDL and the drift check all agree.
    """
    return String().with_variant(VARCHAR(mysql_string_length_for(column_name)), "mysql", "mariadb")


def mysql_text_for(column_name: str) -> Text:
    """``Text`` for general dialects, sized ``VARCHAR`` where MySQL must index it."""
    length = MYSQL_TEXT_AS_VARCHAR.get(column_name.lower())
    if length is None:
        return Text()
    return Text().with_variant(VARCHAR(length), "mysql", "mariadb")


def mysql_binary_for(column_name: str) -> LargeBinary:
    """``LargeBinary`` for general dialects, ``VARBINARY`` where MySQL keys it."""
    if column_name.lower() in MYSQL_BINARY_AS_VARBINARY:
        return LargeBinary().with_variant(VARBINARY(MYSQL_KEYED_BINARY_LENGTH), "mysql", "mariadb")
    return LargeBinary()


def _has_dialect_variant(column_type: Any) -> bool:
    mapping = getattr(column_type, "_variant_mapping", None)
    return bool(mapping)


def _mysql_dimension_length(column_type: Any, column_name: str) -> int:
    """Length for a rollup dimension column rewritten to a binary collation."""
    length = getattr(column_type, "length", None)
    if length:
        return int(length)
    mapping = getattr(column_type, "_variant_mapping", None) or {}
    for key in sorted(MYSQL_DIALECT_NAMES):
        variant = mapping.get(key)
        variant_length = getattr(variant, "length", None)
        if variant_length:
            return int(variant_length)
    return mysql_string_length_for(column_name)


def _size_strings_for_mysql(target: Any, connection: Any, **_: Any) -> None:
    if not is_mysql(connection):
        return

    # MySQL cannot index TEXT/BLOB without a key prefix, and the schema indexes
    # a few Text columns. Decide from the table itself: anything that carries a
    # key (primary, unique, or part of an index) and is Text becomes a sized
    # VARCHAR; everything else keeps MEDIUMTEXT.
    keyed_names: set[str] = set()
    for index in target.indexes:
        for column in index.columns:
            keyed_names.add(column.name)
    for constraint in target.constraints:
        if isinstance(constraint, (PrimaryKeyConstraint, UniqueConstraint)):
            for column in constraint.columns:
                keyed_names.add(column.name)
    for column in target.columns:
        if column.primary_key or column.unique:
            keyed_names.add(column.name)

    for column in target.columns:
        column_type = column.type
        if column.name in MYSQL_SENTINEL_COLUMNS.get(target.name, frozenset()):
            # Rollup dimension columns carry the DIMENSION_SENTINEL encoding;
            # a byte-exact collation keeps the sentinel and the empty string
            # distinct (utf8mb4_0900_ai_ci treats U+001F as ignorable).
            column.type = VARCHAR(
                _mysql_dimension_length(column_type, column.name),
                collation=MYSQL_BINARY_COLLATION,
            )
            continue
        if _has_dialect_variant(column_type):
            # The model already declares this dialect's type explicitly.
            continue
        if isinstance(column_type, LargeBinary):
            # MySQL cannot key a BLOB without a prefix length; binary columns
            # that carry a key are hashes (sha256 = 32 bytes measured), so a
            # VARBINARY is both correct and indexable.
            if column_type.length is None and column.name in keyed_names:
                column.type = VARBINARY(MYSQL_KEYED_BINARY_LENGTH)
            continue
        if isinstance(column_type, DateTime) and column.server_default is not None:
            # The port renders DateTime as ``DATETIME(6)``, and MySQL rejects a
            # second-precision default on a precision-6 column (error 1067:
            # ``DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP`` is invalid).
            # The models and historical migrations spell the default without
            # the precision, so rewrite it here -- which also gives MySQL the
            # microsecond ``now()`` default PostgreSQL has all along.
            rendered = str(getattr(column.server_default, "arg", column.server_default)).strip().lower()
            if "current_timestamp" in rendered or rendered.startswith("now("):
                column.server_default = DefaultClause(text("CURRENT_TIMESTAMP(6)"))
            continue
        if isinstance(column_type, Text):
            override = MYSQL_TEXT_AS_VARCHAR.get(column.name.lower())
            if override is not None:
                column.type = VARCHAR(override)
            elif column.name in keyed_names:
                column.type = VARCHAR(mysql_string_length_for(column.name))
            # MySQL rejects ``DEFAULT 'x'`` on TEXT columns but accepts the
            # expression form ``DEFAULT ('x')`` (8.0.13+); the models declare
            # the literal form, so rewrite it for this dialect.
            default = column.server_default
            literal = getattr(default, "arg", None)
            if literal is not None:
                if isinstance(literal, str):
                    escaped = literal.replace("\\", "\\\\").replace("'", "''")
                    column.server_default = DefaultClause(text(f"('{escaped}')"))
                else:
                    rendered = str(literal)
                    if not rendered.lstrip().startswith("("):
                        column.server_default = DefaultClause(text(f"({rendered})"))
            continue
        if isinstance(column_type, String) and column_type.length is None:
            column.type = VARCHAR(mysql_string_length_for(column.name))


event.listen(Table, "before_create", _size_strings_for_mysql)


def mysql_string(length: int) -> String:
    """A ``String`` whose MySQL/MariaDB DDL is ``VARCHAR(length)``.

    SQLite and PostgreSQL keep the original unbounded ``String`` so their
    generated schema is unchanged; only MySQL sees the sized variant, which it
    requires and which keeps composite index keys inside the 3072-byte utf8mb4
    limit. Lengths are chosen from the measured maxima of the production
    snapshot plus the column's role (id / status / key / label).
    """
    return String().with_variant(VARCHAR(length), "mysql", "mariadb")


@compiles(String, "mysql")
@compiles(String, "mariadb")
def _compile_mysql_string(element, compiler, **kw):  # type: ignore[no-untyped-def]
    # The MariaDB dialect has to be registered explicitly: unlike the MySQL
    # compiler, which defaults a length-less ``VARCHAR`` to 255, the MariaDB
    # compiler raises ``CompileError: VARCHAR requires a length``. Without this
    # the 121 unbounded-``String`` columns in the models broke every migration
    # and drift comparison that ran under a ``mariadb+…`` URL.
    if element.length is None:
        element = element.copy()
        element.length = MYSQL_UNBOUNDED_STRING_LENGTH
    return compiler.visit_string(element, **kw)


@compiles(Text, "mysql")
@compiles(Text, "mariadb")
def _compile_mysql_text(element, compiler, **kw):  # type: ignore[no-untyped-def]
    return MYSQL_TEXT_TYPE


#: ``DateTime`` DDL for MySQL/MariaDB. ``DATETIME(6)`` round-trips microseconds
#: the way SQLite and PostgreSQL do; MySQL's default ``DATETIME`` (fractional
#: seconds 0) rounds them away, sometimes into the next second.
MYSQL_DATETIME_TYPE = "DATETIME(6)"


@compiles(DateTime, "mysql")
@compiles(DateTime, "mariadb")
def _compile_mysql_datetime(element, compiler, **kw):  # type: ignore[no-untyped-def]
    del element, compiler, kw
    return MYSQL_DATETIME_TYPE


@compiles(Float, "mysql")
@compiles(Float, "mariadb")
def _compile_mysql_float(element, compiler, **kw):  # type: ignore[no-untyped-def]
    # MySQL's bare ``FLOAT`` is 4-byte single precision, while SQLite's REAL and
    # PostgreSQL's DOUBLE PRECISION are 8-byte. Cost/token aggregates summed
    # over thousands of rows (and the parity oracles that compare folded sums
    # against raw ones) assume double precision, so a plain Float becomes
    # ``DOUBLE``. An explicit precision in the model still wins: MySQL reads
    # ``FLOAT(p)`` with p > 24 as double precision anyway, and anything narrower
    # is a deliberate choice this hook must not override.
    if element.precision is None and element.asdecimal is False:
        return "DOUBLE"
    return compiler.visit_float(element, **kw)
