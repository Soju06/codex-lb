from __future__ import annotations

import argparse
import logging
import re
import time
import warnings
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, cast

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util.exc import CommandError
from anyio import to_thread
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import Connection

from app.core.config.settings import get_settings
from app.db.alembic.revision_ids import LEGACY_MIGRATION_TO_NEW_REVISION, OLD_TO_NEW_REVISION_MAP, REVISION_ID_PATTERN
from app.db.migration_lock import migration_lock
from app.db.migration_url import to_sync_database_url
from app.db.models import Base

logger = logging.getLogger(__name__)

_ALEMBIC_VERSION_TABLE = "alembic_version"
_ALEMBIC_VERSION_COLUMN = "version_num"
_LEGACY_MIGRATIONS_TABLE = "schema_migrations"
_RUNTIME_SENTINELS_TABLE = "runtime_sentinels"
_REQUIRED_TABLES_FOR_LEGACY_STAMP = frozenset(
    {
        "accounts",
        "usage_history",
        "request_logs",
        "sticky_sessions",
        "dashboard_settings",
    }
)

LEGACY_MIGRATION_ORDER: tuple[str, ...] = (
    "001_normalize_account_plan_types",
    "002_add_request_logs_reasoning_effort",
    "003_add_accounts_reset_at",
    "004_add_accounts_chatgpt_account_id",
    "005_add_dashboard_settings",
    "006_add_dashboard_settings_totp",
    "007_add_dashboard_settings_password",
    "008_add_api_keys",
    "009_add_api_key_limits",
    "010_add_idx_logs_requested_at",
)

LEGACY_TO_REVISION: dict[str, str] = {
    migration_name: LEGACY_MIGRATION_TO_NEW_REVISION[migration_name] for migration_name in LEGACY_MIGRATION_ORDER
}

_BRANCHED_ENFORCEMENT_LEGACY_REVISION = "013_add_api_key_enforcement_fields"
_BRANCHED_ENFORCEMENT_REPAIR_ANCESTOR = OLD_TO_NEW_REVISION_MAP[_BRANCHED_ENFORCEMENT_LEGACY_REVISION]
_BRANCHED_ENFORCEMENT_DESCENDANT_REVISIONS = frozenset(
    {
        "20260225_000000_add_dashboard_settings_routing_strategy",
        "20260228_020000_align_api_key_limit_enum_types",
        "20260228_030000_add_api_firewall_allowlist",
        "20260307_000000_add_api_key_enforcement_fields",
    }
)
_MANUAL_DRIFT_INDEX_REQUIREMENTS: dict[str, frozenset[str]] = {
    "usage_history": frozenset(
        {
            "idx_usage_window_account_latest",
            "idx_usage_window_account_time",
            "idx_usage_window_raw_account_latest",
            "idx_usage_window_account_time_covering",
            "idx_usage_window_raw_account_time_covering",
        }
    ),
    "request_logs": frozenset(
        {
            "idx_logs_requested_at_id",
            "idx_logs_deleted_at_requested_at_id",
            "idx_logs_requested_at_model_tier",
            "idx_logs_model_effort_time",
            "idx_logs_status_error_time",
            "idx_logs_source_requested_at",
            "idx_logs_dash_usage_covering",
            "idx_logs_missing_cost",
            "idx_logs_live_api_key",
            "idx_logs_live_model_effort",
            "idx_logs_live_status_error",
        }
    ),
    "additional_usage_history": frozenset(
        {
            "ix_additional_usage_distinct_labels",
            "ix_additional_usage_alias_limit_latest",
            "ix_additional_usage_alias_feature_latest",
        }
    ),
    "account_limit_warmups": frozenset(
        {
            "idx_account_limit_warmups_account_attempted",
            "idx_account_limit_warmups_status_attempted",
        }
    ),
    "api_keys": frozenset({"idx_api_keys_name"}),
}
_SQLITE_FLOAT_TYPE_COMPAT_COLUMNS = frozenset(
    {
        ("dashboard_settings", "sticky_reallocation_primary_budget_threshold_pct"),
        ("dashboard_settings", "sticky_reallocation_secondary_budget_threshold_pct"),
    }
)
# Columns the ORM no longer maps but the schema still carries. A replica running
# the previous release keeps mapping them and renders explicit NULLs in its
# request-log INSERTs, so the physical drop must wait one release after the
# mapping retirement (the Helm migration Job runs before old replicas drain).
_LEGACY_EXTRA_COLUMNS = frozenset(
    {
        ("request_logs", "slim_summary_json"),
        # Retired from the ORM in retire-prewarm-canary-column-mappings; drop
        # revision + removal from this set is queued for the following release.
        ("request_logs", "prewarm_canary_bucket"),
        ("request_logs", "prewarm_eligible_reason"),
    }
)


@dataclass(frozen=True)
class LegacyBootstrapResult:
    stamped_revision: str | None
    legacy_row_count: int
    unknown_migrations: tuple[str, ...]
    had_non_contiguous_entries: bool


@dataclass(frozen=True)
class MigrationRunResult:
    current_revision: str | None
    bootstrap: LegacyBootstrapResult


@dataclass(frozen=True)
class MigrationState:
    current_revision: str | None
    head_revision: str
    has_alembic_version_table: bool
    has_legacy_migrations_table: bool
    needs_upgrade: bool
    unknown_revisions: tuple[str, ...]
    is_ahead: bool


class MigrationBootstrapError(RuntimeError):
    pass


def _script_location() -> str:
    return str((Path(__file__).resolve().parent / "alembic").resolve())


def _build_alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", _script_location())
    sync_database_url = to_sync_database_url(database_url)
    # `to_sync_database_url` routes the URL through `make_url().render_as_string()`,
    # which percent-encodes the path on Windows (e.g.
    # `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`). Alembic stores option values in
    # a `configparser` using `BasicInterpolation`, which treats a bare `%` as
    # interpolation syntax and raises `ValueError: invalid interpolation syntax`
    # from `set_main_option`. Escape `%` -> `%%` so ConfigParser keeps the value
    # verbatim; `get_main_option` decodes `%%` back to `%`, so the URL handed to
    # SQLAlchemy is unchanged.
    config.set_main_option("sqlalchemy.url", sync_database_url.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    return config


def _required_sqlalchemy_url(config: Config) -> str:
    sync_database_url = config.get_main_option("sqlalchemy.url")
    if not sync_database_url:
        raise MigrationBootstrapError("sqlalchemy.url is missing in alembic config")
    return sync_database_url


@contextmanager
def _sync_connection(sync_database_url: str) -> Iterator[Connection]:
    engine = create_engine(sync_database_url, future=True)
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


@contextmanager
def _sync_transaction(sync_database_url: str) -> Iterator[Connection]:
    engine = create_engine(sync_database_url, future=True)
    try:
        with engine.begin() as connection:
            yield connection
    finally:
        engine.dispose()


def _read_table_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    return set(inspector.get_table_names())


def _read_legacy_migration_names(connection: Connection) -> set[str]:
    result = connection.execute(text(f"SELECT name FROM {_LEGACY_MIGRATIONS_TABLE}"))
    names = {str(row[0]) for row in result.fetchall() if row and row[0] is not None}
    return names


def _read_current_revisions_from_connection(connection: Connection) -> tuple[str, ...]:
    try:
        rows = connection.execute(text(f"SELECT {_ALEMBIC_VERSION_COLUMN} FROM {_ALEMBIC_VERSION_TABLE}")).fetchall()
    except (sa_exc.ProgrammingError, sa_exc.OperationalError) as exc:
        # PostgreSQL can still raise UndefinedTable here on a fresh database if
        # the alembic_version table is absent when startup migration state is
        # re-read. SQLite raises OperationalError for the same missing-table
        # path. Treat both the same as "no revision yet".
        message = str(exc).lower()
        if _ALEMBIC_VERSION_TABLE in message and (
            "does not exist" in message or "undefinedtable" in message or "no such table" in message
        ):
            return ()
        raise
    revisions = {str(row[0]) for row in rows if row and row[0]}
    return tuple(sorted(revisions))


def _read_current_revision_from_connection(connection: Connection) -> str | None:
    revisions = list(_read_current_revisions_from_connection(connection))
    if not revisions:
        return None
    if len(revisions) == 1:
        return revisions[0]
    return ",".join(sorted(revisions))


def _contiguous_prefix_count(applied: set[str]) -> int:
    contiguous = 0
    for migration_name in LEGACY_MIGRATION_ORDER:
        if migration_name in applied:
            contiguous += 1
            continue
        break
    return contiguous


def _detect_non_contiguous_entries(applied: set[str], contiguous_prefix_count: int) -> bool:
    trailing = LEGACY_MIGRATION_ORDER[contiguous_prefix_count:]
    return any(name in applied for name in trailing)


def _missing_required_legacy_tables_for_stamp(tables: set[str]) -> tuple[str, ...]:
    return tuple(sorted(table for table in _REQUIRED_TABLES_FOR_LEGACY_STAMP if table not in tables))


def _bootstrap_legacy_history(config: Config) -> LegacyBootstrapResult:
    sync_database_url = _required_sqlalchemy_url(config)

    with _sync_connection(sync_database_url) as connection:
        tables = _read_table_names(connection)
        if _ALEMBIC_VERSION_TABLE in tables:
            return LegacyBootstrapResult(
                stamped_revision=None,
                legacy_row_count=0,
                unknown_migrations=(),
                had_non_contiguous_entries=False,
            )

        if _LEGACY_MIGRATIONS_TABLE not in tables:
            return LegacyBootstrapResult(
                stamped_revision=None,
                legacy_row_count=0,
                unknown_migrations=(),
                had_non_contiguous_entries=False,
            )

        applied = _read_legacy_migration_names(connection)

    if not applied:
        return LegacyBootstrapResult(
            stamped_revision=None,
            legacy_row_count=0,
            unknown_migrations=(),
            had_non_contiguous_entries=False,
        )

    unknown = tuple(sorted(name for name in applied if name not in LEGACY_TO_REVISION))
    contiguous_count = _contiguous_prefix_count(applied)
    has_non_contiguous = _detect_non_contiguous_entries(applied, contiguous_count)

    if contiguous_count <= 0:
        return LegacyBootstrapResult(
            stamped_revision=None,
            legacy_row_count=len(applied),
            unknown_migrations=unknown,
            had_non_contiguous_entries=has_non_contiguous,
        )

    missing_required_tables = _missing_required_legacy_tables_for_stamp(tables)
    if missing_required_tables:
        logger.warning(
            "Skipping legacy bootstrap stamp due to missing required tables tables=%s",
            missing_required_tables,
        )
        return LegacyBootstrapResult(
            stamped_revision=None,
            legacy_row_count=len(applied),
            unknown_migrations=unknown,
            had_non_contiguous_entries=has_non_contiguous,
        )

    target_legacy_name = LEGACY_MIGRATION_ORDER[contiguous_count - 1]
    target_revision = LEGACY_TO_REVISION[target_legacy_name]
    _ensure_alembic_version_table_capacity(config)
    command.stamp(config, target_revision)

    return LegacyBootstrapResult(
        stamped_revision=target_revision,
        legacy_row_count=len(applied),
        unknown_migrations=unknown,
        had_non_contiguous_entries=has_non_contiguous,
    )


def _read_current_revision(sync_database_url: str) -> str | None:
    with _sync_connection(sync_database_url) as connection:
        tables = _read_table_names(connection)
        if _ALEMBIC_VERSION_TABLE not in tables:
            return None
        return _read_current_revision_from_connection(connection)


def _head_revision(config: Config) -> str:
    script_directory = ScriptDirectory.from_config(config)
    heads = sorted(script_directory.get_heads())
    if not heads:
        raise MigrationBootstrapError("No Alembic head revision found")
    if len(heads) == 1:
        return heads[0]
    return ",".join(heads)


def _known_revisions(config: Config) -> set[str]:
    script_directory = ScriptDirectory.from_config(config)
    return {revision.revision for revision in script_directory.walk_revisions() if revision.revision}


def _max_revision_id_length(config: Config) -> int:
    script_directory = ScriptDirectory.from_config(config)
    lengths = [len(revision.revision) for revision in script_directory.walk_revisions() if revision.revision]
    if not lengths:
        raise MigrationBootstrapError("No Alembic revisions found")
    return max(lengths)


def _ensure_alembic_version_table_capacity_for_connection(connection: Connection, *, required_length: int) -> None:
    if connection.dialect.name != "postgresql":
        return

    inspector = inspect(connection)
    if not inspector.has_table(_ALEMBIC_VERSION_TABLE):
        # IF NOT EXISTS is defense-in-depth against concurrent out-of-band
        # `alembic upgrade` invocations that bypass run_upgrade's migration
        # lock; the product paths are already serialized by migration_lock.
        connection.execute(
            text(
                " ".join(
                    (
                        f"CREATE TABLE IF NOT EXISTS {_ALEMBIC_VERSION_TABLE} (",
                        f"{_ALEMBIC_VERSION_COLUMN} VARCHAR({required_length}) NOT NULL,",
                        f"PRIMARY KEY ({_ALEMBIC_VERSION_COLUMN})",
                        ")",
                    )
                )
            )
        )
        return

    columns = inspector.get_columns(_ALEMBIC_VERSION_TABLE)
    version_num_column = next((column for column in columns if column.get("name") == _ALEMBIC_VERSION_COLUMN), None)
    if version_num_column is None:
        raise MigrationBootstrapError(
            f"{_ALEMBIC_VERSION_TABLE}.{_ALEMBIC_VERSION_COLUMN} is missing from migration metadata table"
        )
    version_num_type = version_num_column.get("type")
    version_num_length = getattr(version_num_type, "length", None)
    if version_num_length is None or version_num_length >= required_length:
        return

    connection.execute(
        text(
            f"ALTER TABLE {_ALEMBIC_VERSION_TABLE} "
            f"ALTER COLUMN {_ALEMBIC_VERSION_COLUMN} TYPE VARCHAR({required_length})"
        )
    )


def _ensure_alembic_version_table_capacity(config: Config) -> None:
    sync_database_url = _required_sqlalchemy_url(config)
    required_length = _max_revision_id_length(config)
    with _sync_transaction(sync_database_url) as connection:
        _ensure_alembic_version_table_capacity_for_connection(connection, required_length=required_length)


def _collect_migration_policy_violations(config: Config) -> tuple[str, ...]:
    violations: list[str] = []
    script_directory = ScriptDirectory.from_config(config)

    heads = sorted(script_directory.get_heads())
    if len(heads) != 1:
        violations.append(f"alembic_head_count_invalid expected=1 actual={len(heads)} heads={','.join(heads)}")

    seen_revision_ids: set[str] = set()
    for revision in script_directory.walk_revisions():
        revision_id = revision.revision
        if not revision_id:
            continue

        if revision_id in seen_revision_ids:
            violations.append(f"alembic_revision_duplicate revision={revision_id}")
        else:
            seen_revision_ids.add(revision_id)

        if not REVISION_ID_PATTERN.fullmatch(revision_id):
            violations.append(f"alembic_revision_id_format_invalid revision={revision_id}")

        revision_path = getattr(revision, "path", None)
        if revision_path:
            actual_name = Path(str(revision_path)).name
            expected_name = f"{revision_id}.py"
            if actual_name != expected_name:
                violations.append(
                    "alembic_revision_filename_mismatch "
                    f"revision={revision_id} expected={expected_name} actual={actual_name}"
                )

    return tuple(sorted(violations))


def check_migration_policy(database_url: str) -> tuple[str, ...]:
    config = _build_alembic_config(database_url)
    return _collect_migration_policy_violations(config)


def _remap_legacy_alembic_revisions(config: Config) -> tuple[str, ...]:
    sync_database_url = _required_sqlalchemy_url(config)
    known_revisions = _known_revisions(config)

    with _sync_transaction(sync_database_url) as connection:
        tables = _read_table_names(connection)
        if _ALEMBIC_VERSION_TABLE not in tables:
            return ()

        current_revisions = _read_current_revisions_from_connection(connection)
        if not current_revisions:
            return ()

        unsupported = tuple(
            sorted(
                revision
                for revision in current_revisions
                if revision not in known_revisions and revision not in OLD_TO_NEW_REVISION_MAP
            )
        )
        if unsupported:
            raise MigrationBootstrapError(
                "Unsupported alembic_version revision ids detected; manual intervention required "
                f"unsupported={','.join(unsupported)}"
            )

        remapped_set = {OLD_TO_NEW_REVISION_MAP.get(revision, revision) for revision in current_revisions}

        if (
            _BRANCHED_ENFORCEMENT_LEGACY_REVISION in current_revisions
            and _BRANCHED_ENFORCEMENT_REPAIR_ANCESTOR in remapped_set
            and remapped_set & _BRANCHED_ENFORCEMENT_DESCENDANT_REVISIONS
        ):
            remapped_set.discard(_BRANCHED_ENFORCEMENT_REPAIR_ANCESTOR)

        remapped = tuple(sorted(remapped_set))
        if remapped == current_revisions:
            return ()

        connection.execute(text(f"DELETE FROM {_ALEMBIC_VERSION_TABLE}"))
        for revision in remapped:
            connection.execute(
                text(
                    f"INSERT INTO {_ALEMBIC_VERSION_TABLE} ({_ALEMBIC_VERSION_COLUMN}) "
                    f"VALUES (:{_ALEMBIC_VERSION_COLUMN})"
                ),
                {_ALEMBIC_VERSION_COLUMN: revision},
            )

    logger.info(
        "Remapped legacy alembic revision ids old=%s new=%s",
        current_revisions,
        remapped,
    )
    return current_revisions


def inspect_migration_state(database_url: str) -> MigrationState:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)
    head_revision = _head_revision(config)

    with _sync_connection(sync_database_url) as connection:
        tables = _read_table_names(connection)
        has_alembic = _ALEMBIC_VERSION_TABLE in tables
        has_legacy = _LEGACY_MIGRATIONS_TABLE in tables
        current_revisions = _read_current_revisions_from_connection(connection) if has_alembic else ()

    if not current_revisions:
        current = None
    elif len(current_revisions) == 1:
        current = current_revisions[0]
    else:
        current = ",".join(current_revisions)

    known_revisions = _known_revisions(config)
    unknown_revisions = tuple(
        sorted(
            revision
            for revision in current_revisions
            if revision not in known_revisions and revision not in OLD_TO_NEW_REVISION_MAP
        )
    )

    if has_alembic:
        needs_upgrade = current != head_revision
    else:
        # Missing alembic_version always requires bootstrap and/or upgrade.
        needs_upgrade = True

    return MigrationState(
        current_revision=current,
        head_revision=head_revision,
        has_alembic_version_table=has_alembic,
        has_legacy_migrations_table=has_legacy,
        needs_upgrade=needs_upgrade,
        unknown_revisions=unknown_revisions,
        is_ahead=bool(unknown_revisions),
    )


def _sqlite_index_names(connection: Connection, table_name: str) -> set[str]:
    escaped_name = table_name.replace('"', '""')
    rows = connection.execute(text(f'PRAGMA index_list("{escaped_name}")')).fetchall()
    return {str(row[1]) for row in rows if len(row) > 1 and row[1] is not None}


def _postgresql_index_names(connection: Connection, table_name: str) -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = ANY (current_schemas(false))
              AND tablename = :table_name
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows if row and row[0] is not None}


def _read_index_names_for_drift(connection: Connection, table_name: str) -> set[str]:
    if connection.dialect.name == "sqlite":
        return _sqlite_index_names(connection, table_name)
    if connection.dialect.name == "postgresql":
        return _postgresql_index_names(connection, table_name)

    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _manual_schema_drift_diffs(connection: Connection) -> tuple[str, ...]:
    diffs: list[str] = []
    for table_name, required_indexes in _MANUAL_DRIFT_INDEX_REQUIREMENTS.items():
        existing_indexes = _read_index_names_for_drift(connection, table_name)
        for index_name in sorted(required_indexes - existing_indexes):
            diffs.append(repr(("missing_index", table_name, index_name)))
    return tuple(diffs)


def _unwrap_schema_drift_diff(diff: object) -> object:
    if isinstance(diff, list) and len(diff) == 1:
        return diff[0]
    return diff


def _is_ignored_schema_drift(connection: Connection, diff: object) -> bool:
    diff = _unwrap_schema_drift_diff(diff)
    if not isinstance(diff, tuple) or not diff:
        return False

    if diff[0] == "remove_column" and len(diff) >= 4:
        column = diff[3]
        column_name = getattr(column, "name", None)
        if (str(diff[2]), str(column_name)) in _LEGACY_EXTRA_COLUMNS:
            return True

    if connection.dialect.name == "sqlite" and diff[0] == "modify_type" and len(diff) >= 7:
        table_name = str(diff[2])
        column_name = str(diff[3])
        if (table_name, column_name) not in _SQLITE_FLOAT_TYPE_COMPAT_COLUMNS:
            return False
        existing_type = diff[5]
        metadata_type = diff[6]
        return str(existing_type).upper() == "REAL" and str(metadata_type).upper() in {"FLOAT", "REAL"}

    return False


_OBJECT_ADDRESS_RE = re.compile(r" object at 0x[0-9a-fA-F]+>")


def _stable_diff_repr(diff: object) -> str:
    """Render an autogenerate diff without CPython object addresses.

    SQLAlchemy renders constraint members as ``<... object at 0x7f...>``, so the
    repr of an otherwise identical foreign-key diff differs between two calls in
    the same process. Drift output is compared and shown to operators, so it has
    to be stable.
    """
    return _OBJECT_ADDRESS_RE.sub(" object>", repr(diff))


def check_schema_drift(database_url: str) -> tuple[str, ...]:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)

    with _sync_connection(sync_database_url) as connection:
        migration_context = MigrationContext.configure(
            connection=connection,
            opts={
                "target_metadata": Base.metadata,
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"Skipped unsupported reflection of expression-based index .*",
            )
            warnings.filterwarnings(
                "ignore",
                message=r"autogenerate skipping metadata-specified expression-based index .*",
            )
            diffs = [
                diff
                for diff in compare_metadata(migration_context, Base.metadata)
                if not _is_ignored_schema_drift(connection, diff)
            ]
        manual_diffs = _manual_schema_drift_diffs(connection)

    return tuple(_stable_diff_repr(diff) for diff in diffs) + manual_diffs


_NO_LEGACY_BOOTSTRAP = LegacyBootstrapResult(
    stamped_revision=None,
    legacy_row_count=0,
    unknown_migrations=(),
    had_non_contiguous_entries=False,
)


def _resolved_lock_timeout_seconds(lock_timeout_seconds: float | None) -> float:
    if lock_timeout_seconds is not None:
        return lock_timeout_seconds
    return get_settings().database_migration_lock_timeout_seconds


#: The revision that copied the legacy dashboard credentials onto the account
#: row (release N), and the one that drops the columns they lived in (this
#: release). The drop **descends** from the re-projection, so any upgrade that
#: crosses the drop re-projects first, in the same run: a database stamped at
#: any older revision reaches head in one command with its credentials intact,
#: and refusing that jump would keep every existing install from starting.
CREDENTIAL_REPROJECTION_REVISION = "20260909_020000_reproject_compat_admin_credentials"
CREDENTIAL_DROP_REVISION = "20260912_010000_drop_legacy_dashboard_credentials"

#: The table the dropped columns live in, and therefore the evidence that there
#: is an install here at all: a replica of an earlier release maps those columns
#: and loads this row whole, so until the table exists there is nothing for one
#: to be serving and nothing to drain.
_CREDENTIAL_MIRROR_TABLE = "dashboard_settings"

_CREDENTIAL_DROP_DRAIN_WARNING = (
    "Dropping the legacy dashboard credential columns (%s). Replicas of any earlier release map these "
    "columns and load the settings row whole, so their settings reads fail once this commits: stop them "
    "before this migration runs, not after. Rollback is supported to the immediately previous release only."
)


#: Durable "the legacy credential layer is retired" marker, written into
#: ``runtime_sentinels`` by ``CREDENTIAL_DROP_REVISION`` and read back here.
#: Frozen as a literal on both sides for the same reason the revision freezes
#: its identifiers: neither may import the other.
LEGACY_CREDENTIALS_RETIRED_SENTINEL = "dashboard_legacy_credentials_retired"

_LEDGER_BEHIND_SCHEMA_WARNING = (
    "Database has already retired the legacy dashboard credential columns (%s recorded it in "
    "%s), but its Alembic ledger says %s, which does not include that revision. Stamping the "
    "ledger at it rather than replaying the chain over this schema: the replay re-creates those "
    "columns empty and the re-projection between them reads that emptiness as a removed "
    "password, which would clear the account row that now holds the only dashboard credential "
    "this install has."
)


def _remapped(revision: str) -> str:
    return OLD_TO_NEW_REVISION_MAP.get(revision, revision)


def _pending_revisions(config: Config, current_revisions: Sequence[str], revision: str) -> frozenset[str]:
    """Exactly the revisions ``command.upgrade(config, revision)`` would apply.

    ``revision`` is whatever the caller passed, which is every target form
    Alembic accepts: ``head``/``heads``, a full id, an unambiguous id *prefix*,
    and a relative spec such as ``+1`` or ``<prefix>+2``. Resolving it by hand
    -- special-casing ``head`` and otherwise treating the string as a literal
    id -- silently mis-reads the last three: they resolve fine inside
    ``command.upgrade`` a moment later, so the drop would run with nothing
    said. This asks ``ScriptDirectory._upgrade_revs``, which is the function
    ``command.upgrade`` itself calls to plan the run, so the check and the
    command cannot disagree. ``iterate_revisions`` is the public spelling and
    was the first attempt; it raises ``RangeNotAncestorError`` for a ledger
    holding several heads when the target is on one branch, where the upgrade
    succeeds -- one resolver, not two, is the point.

    Empty on anything unanswerable: an id this build does not know (the caller
    is a schema stamped ahead, which ``_run_upgrade_locked`` refuses on its own)
    or a target Alembic itself cannot resolve (it raises the same error a beat
    later, and a warning about a command that is going to fail helps nobody).
    """

    script_directory = ScriptDirectory.from_config(config)
    known = _known_revisions(config)
    resolved_current = tuple(_remapped(item) for item in current_revisions)
    if any(item not in known for item in resolved_current):
        return frozenset()
    try:
        # Alembic annotates ``current_rev`` as ``str``, but ``command.upgrade``
        # itself hands it the migration context's *tuple* of heads and the
        # resolver takes any collection. Narrowing to one would mis-plan a
        # ledger that holds several.
        steps = script_directory._upgrade_revs(revision, cast("Any", resolved_current))
    except (CommandError, RevisionError):
        return frozenset()
    return frozenset(step.revision.revision for step in steps if step.revision.revision)


def _check_legacy_credential_drop(config: Config, sync_database_url: str, revision: str) -> None:
    """Read the ledger *and* the schema, then decide; no side effects, no DDL."""

    with _sync_connection(sync_database_url) as connection:
        tables = _read_table_names(connection)
        current_revisions = (
            _read_current_revisions_from_connection(connection) if _ALEMBIC_VERSION_TABLE in tables else ()
        )
        has_existing_schema = _CREDENTIAL_MIRROR_TABLE in tables
    check_legacy_credential_drop(
        config,
        current_revisions,
        revision,
        has_existing_schema=has_existing_schema,
    )


def check_legacy_credential_drop(
    config: Config,
    current_revisions: Sequence[str],
    revision: str,
    *,
    has_existing_schema: bool,
) -> None:
    """State the drain requirement before the legacy credential columns go.

    Called before any DDL, and it deliberately claims only what the ledger and
    the schema can show. Nothing is refused: ``CREDENTIAL_REPROJECTION_REVISION``
    is an ancestor of ``CREDENTIAL_DROP_REVISION``, so a database stamped
    anywhere below both reaches head in one command with the credentials copied
    onto the account rows before the columns they came from disappear, and the
    ordering that makes that true is pinned by a test rather than re-checked
    here. What no signal anywhere can show is whether a replica of an earlier
    release is serving right now, so the drain requirement is a warning an
    operator must act on rather than a condition this process can verify.

    An empty ledger is *not* on its own a fresh install, which is why
    ``has_existing_schema`` is asked for separately. ``alembic_version`` can be
    absent, truncated, or restored without its rows over a schema that has been
    serving for months, and that database reads identically to a new one -- yet
    it is the one that most needs to be told, because the replay it is about to
    run reaches ``CREDENTIAL_DROP_REVISION`` and drops the columns for real (the
    chain's early revisions are inspector-guarded, so replaying over an existing
    schema does not fail first). The fresh case is therefore read off the data:
    ``_CREDENTIAL_MIRROR_TABLE`` is where the doomed columns live, and until it
    exists there is no install here for a replica of an earlier release to be
    serving. Both halves have to hold -- an empty ledger *and* no such table.

    ``config.attributes["codex_lb_fresh_install"]`` is deliberately not that
    signal: it is false whenever an ``alembic_version`` table merely exists, so
    an empty one left behind by a crashed first run would turn today's missing
    warning into a warning about a database with nothing to drain -- and a
    published revision already reads that attribute to pick its defaults, so it
    is not free to redefine.
    """

    if not current_revisions and not has_existing_schema:
        # No ledger and no schema: a fresh install. The chain creates the
        # tables without the dropped columns ever holding anything.
        return

    if CREDENTIAL_DROP_REVISION in _pending_revisions(config, current_revisions, revision):
        logger.warning(_CREDENTIAL_DROP_DRAIN_WARNING, CREDENTIAL_DROP_REVISION)


def _read_runtime_sentinel(connection: Connection, name: str) -> str | None:
    if _RUNTIME_SENTINELS_TABLE not in _read_table_names(connection):
        return None
    row = connection.execute(
        text(f"SELECT value FROM {_RUNTIME_SENTINELS_TABLE} WHERE name = :name"),
        {"name": name},
    ).first()
    return None if row is None else str(row[0])


def _ledger_has_applied(config: Config, current_revisions: Sequence[str], revision: str) -> bool:
    """Whether ``revision`` is at or below any of ``current_revisions``."""

    script_directory = ScriptDirectory.from_config(config)
    return any(
        item.revision == revision
        for current in current_revisions
        for item in script_directory.iterate_revisions(current, "base")
    )


def _reconcile_retired_credential_ledger(config: Config) -> str | None:
    """Re-stamp a ledger that sits below a schema which already retired the credentials.

    The same shape as ``_bootstrap_legacy_history``: a database whose ledger
    does not describe it is placed at the revision its own data proves it
    reached, rather than replayed. The evidence is the marker
    ``CREDENTIAL_DROP_REVISION`` writes into ``runtime_sentinels``, and only
    that revision writes it (its downgrade removes it again), so the marker
    means the three columns are gone -- whatever the ledger says. A ledger that
    disagrees has been lost, rewound or restored from a partial backup, and it
    is the ledger that is wrong.

    The evidence has to be data, because the ledger is the thing that went
    missing. And the replay it prevents is not merely wasteful, it is
    destructive: ``20260213_000600``/``20260213_000700`` re-add
    ``password_hash``, ``totp_secret_encrypted`` and ``totp_last_verified_step``
    to an existing ``dashboard_settings`` as **empty** columns, and
    ``CREDENTIAL_REPROJECTION_REVISION`` then reads that emptiness as "the
    previous release removed the password" and NULLs the credential on the
    account row -- which, after this release, is the only copy, so the install
    is locked out of its own dashboard. (Replaying
    ``20260909_010000_add_dashboard_users`` over a schema whose ledger was
    rewound *past* the column re-creation fails outright instead, on
    ``no such column: password_hash``.) Both of those revisions are published
    and must not be edited to defend themselves -- an install that has applied
    a revision never applies it again -- so the defence is here, before the
    first one runs.

    Call this *after* ``_remap_legacy_alembic_revisions``: it needs a ledger
    Alembic can resolve, and a ledger it cannot is left alone so the upgrade
    raises its own error rather than a stamp raising one earlier.

    Returns the stamped revision, or ``None`` when nothing was stamped.
    """

    sync_database_url = _required_sqlalchemy_url(config)
    with _sync_connection(sync_database_url) as connection:
        has_ledger = _ALEMBIC_VERSION_TABLE in _read_table_names(connection)
        marker = _read_runtime_sentinel(connection, LEGACY_CREDENTIALS_RETIRED_SENTINEL)
        current_revisions = _read_current_revisions_from_connection(connection) if has_ledger else ()

    if marker is None:
        return None
    if any(revision not in _known_revisions(config) for revision in current_revisions):
        return None
    if current_revisions and _ledger_has_applied(config, current_revisions, CREDENTIAL_DROP_REVISION):
        return None

    logger.warning(
        _LEDGER_BEHIND_SCHEMA_WARNING,
        marker,
        _RUNTIME_SENTINELS_TABLE,
        ",".join(current_revisions) if current_revisions else f"no {_ALEMBIC_VERSION_TABLE} table",
    )
    _ensure_alembic_version_table_capacity(config)
    command.stamp(config, CREDENTIAL_DROP_REVISION)
    return CREDENTIAL_DROP_REVISION


def _schema_ahead_error(state: MigrationState) -> MigrationBootstrapError:
    return MigrationBootstrapError(
        f"Database schema revision(s) {','.join(state.unknown_revisions)} are not known to this build "
        f"(head={state.head_revision}); the schema was likely migrated by a newer version. "
        "Deploy a matching or newer image, or run an Alembic downgrade to a revision this build knows."
    )


def run_upgrade(
    database_url: str,
    revision: str = "head",
    *,
    bootstrap_legacy: bool,
    auto_remap_legacy_revisions: bool = True,
    lock_timeout_seconds: float | None = None,
) -> MigrationRunResult:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)
    with migration_lock(
        sync_database_url,
        timeout_seconds=_resolved_lock_timeout_seconds(lock_timeout_seconds),
    ):
        return _run_upgrade_locked(
            config,
            database_url,
            revision,
            bootstrap_legacy=bootstrap_legacy,
            auto_remap_legacy_revisions=auto_remap_legacy_revisions,
        )


def _run_upgrade_locked(
    config: Config,
    database_url: str,
    revision: str,
    *,
    bootstrap_legacy: bool,
    auto_remap_legacy_revisions: bool,
) -> MigrationRunResult:
    state_before = inspect_migration_state(database_url)
    if state_before.is_ahead:
        raise _schema_ahead_error(state_before)

    # Post-acquire re-check: a peer holding the lock first may already have
    # migrated to head. When alembic_version exists the legacy bootstrap never
    # stamps, and current == head means no legacy remap is pending, so skipping
    # is safe and lets the losing replica complete startup successfully.
    if revision == "head" and state_before.has_alembic_version_table and not state_before.needs_upgrade:
        logger.info(
            "Database schema already at Alembic head revision=%s; skipping upgrade "
            "(migrations were applied by this or another replica)",
            state_before.current_revision,
        )
        return MigrationRunResult(
            current_revision=state_before.current_revision,
            bootstrap=_NO_LEGACY_BOOTSTRAP,
        )

    config.attributes["codex_lb_fresh_install"] = (
        state_before.current_revision is None
        and not state_before.has_alembic_version_table
        and not state_before.has_legacy_migrations_table
    )

    bootstrap_result = _NO_LEGACY_BOOTSTRAP

    if bootstrap_legacy:
        bootstrap_result = _bootstrap_legacy_history(config)
        if bootstrap_result.stamped_revision is not None:
            config.attributes["codex_lb_fresh_install"] = False

    _ensure_alembic_version_table_capacity(config)
    if auto_remap_legacy_revisions:
        _remap_legacy_alembic_revisions(config)
    # The second ledger repair, and the one that must not be skipped: replaying
    # the chain over a schema that already retired the legacy credential
    # columns destroys the account credentials (see the function). It runs
    # after the remap because it needs a ledger Alembic can resolve.
    if _reconcile_retired_credential_ledger(config) is not None:
        config.attributes["codex_lb_fresh_install"] = False
    # Last read before the first DDL: the legacy bootstrap, the remap and the
    # reconciliation may all have moved the ledger, and the question is what
    # this database has actually applied at the moment the upgrade starts.
    _check_legacy_credential_drop(config, _required_sqlalchemy_url(config), revision)
    command.upgrade(config, revision)

    sync_database_url = _required_sqlalchemy_url(config)
    current_revision = _read_current_revision(sync_database_url)

    if bootstrap_result.unknown_migrations:
        logger.warning(
            "Unknown legacy migration names detected names=%s",
            bootstrap_result.unknown_migrations,
        )
    if bootstrap_result.had_non_contiguous_entries:
        logger.warning("Legacy migration table has non-contiguous applied entries")

    return MigrationRunResult(current_revision=current_revision, bootstrap=bootstrap_result)


async def run_startup_migrations(database_url: str) -> MigrationRunResult:
    auto_remap = get_settings().database_alembic_auto_remap_enabled
    return await to_thread.run_sync(
        lambda: run_upgrade(
            database_url,
            "head",
            bootstrap_legacy=True,
            auto_remap_legacy_revisions=auto_remap,
        ),
    )


def current_revision(database_url: str) -> str | None:
    state = inspect_migration_state(database_url)
    return state.current_revision


def stamp_revision(database_url: str, revision: str, *, lock_timeout_seconds: float | None = None) -> None:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)
    with migration_lock(
        sync_database_url,
        timeout_seconds=_resolved_lock_timeout_seconds(lock_timeout_seconds),
    ):
        _ensure_alembic_version_table_capacity(config)
        command.stamp(config, revision)


def wait_for_connection(
    database_url: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = 2.0,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")

    started_at = time.monotonic()
    last_error: Exception | None = None
    sync_database_url = to_sync_database_url(database_url)

    while True:
        try:
            with _sync_connection(sync_database_url):
                return
        except Exception as exc:
            last_error = exc
        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            if last_error is not None:
                raise TimeoutError(
                    f"Timed out waiting for database connectivity after {timeout_seconds:.1f}s: {last_error}"
                ) from last_error
            raise TimeoutError(f"Timed out waiting for database connectivity after {timeout_seconds:.1f}s")
        time.sleep(min(interval_seconds, timeout_seconds - elapsed))


def wait_for_head(
    database_url: str,
    *,
    timeout_seconds: float,
    interval_seconds: float = 2.0,
) -> MigrationState:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")

    started_at = time.monotonic()
    last_error: Exception | None = None

    while True:
        try:
            state = inspect_migration_state(database_url)
            if not state.needs_upgrade:
                return state
        except Exception as exc:
            last_error = exc
        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            if last_error is not None:
                raise TimeoutError(
                    f"Timed out waiting for database schema to reach Alembic head after {timeout_seconds:.1f}s: "
                    f"{last_error}"
                ) from last_error
            raise TimeoutError(
                f"Timed out waiting for database schema to reach Alembic head after {timeout_seconds:.1f}s"
            )
        time.sleep(min(interval_seconds, timeout_seconds - elapsed))


def _non_empty_database_url(value: str) -> str:
    if value == "":
        raise argparse.ArgumentTypeError("database URL must not be empty")
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Database migration utility for codex-lb.")
    parser.add_argument(
        "--db-url",
        type=_non_empty_database_url,
        default=None,
        help="Database URL to migrate. Defaults to CODEX_LB_DATABASE_URL from settings.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    upgrade_parser = subparsers.add_parser("upgrade", help="Upgrade schema to a target revision.")
    upgrade_parser.add_argument("revision", nargs="?", default="head")
    upgrade_parser.add_argument(
        "--no-bootstrap-legacy",
        action="store_true",
        help="Disable automatic legacy schema_migrations bootstrap before upgrade.",
    )
    upgrade_parser.add_argument(
        "--no-auto-remap-legacy-revisions",
        action="store_true",
        help="Disable automatic remap of legacy Alembic revision IDs.",
    )

    subparsers.add_parser("current", help="Print current alembic revision.")

    subparsers.add_parser("check", help="Check Alembic policy and model/schema drift.")

    wait_parser = subparsers.add_parser(
        "wait-for-head",
        help="Wait until the database schema reaches Alembic head without applying migrations locally.",
    )
    wait_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum seconds to wait for the schema to reach Alembic head.",
    )
    wait_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval in seconds while waiting for the schema to reach Alembic head.",
    )

    connect_parser = subparsers.add_parser(
        "wait-for-connection",
        help="Wait until the database accepts connections without applying migrations locally.",
    )
    connect_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Maximum seconds to wait for database connectivity.",
    )
    connect_parser.add_argument(
        "--interval-seconds",
        type=float,
        default=2.0,
        help="Polling interval in seconds while waiting for database connectivity.",
    )

    stamp_parser = subparsers.add_parser("stamp", help="Set current revision without running migrations.")
    stamp_parser.add_argument("revision")

    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    database_url = get_settings().database_url if args.db_url is None else args.db_url

    if args.command == "upgrade":
        result = run_upgrade(
            database_url,
            args.revision,
            bootstrap_legacy=not bool(args.no_bootstrap_legacy),
            auto_remap_legacy_revisions=not bool(args.no_auto_remap_legacy_revisions),
        )
        print(f"current_revision={result.current_revision or 'none'}")
        if result.bootstrap.stamped_revision:
            print(f"legacy_bootstrap_stamped={result.bootstrap.stamped_revision}")
        return

    if args.command == "current":
        revision = current_revision(database_url)
        print(revision or "none")
        return

    if args.command == "check":
        policy_violations = check_migration_policy(database_url)
        if policy_violations:
            print("migration_policy_violations_detected")
            for violation in policy_violations:
                print(violation)
        drift = check_schema_drift(database_url)
        if drift:
            print("schema_drift_detected")
            for diff in drift:
                print(diff)
        if policy_violations or drift:
            raise SystemExit(1)
        print("migration_policy=ok")
        print("schema_drift=none")
        return

    if args.command == "stamp":
        stamp_revision(database_url, args.revision)
        print(f"stamped={args.revision}")
        return

    if args.command == "wait-for-head":
        state = wait_for_head(
            database_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        print(f"current_revision={state.current_revision or 'none'}")
        print(f"head_revision={state.head_revision}")
        return

    if args.command == "wait-for-connection":
        wait_for_connection(
            database_url,
            timeout_seconds=args.timeout_seconds,
            interval_seconds=args.interval_seconds,
        )
        print("database_connection=ready")
        return

    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
