from __future__ import annotations

import argparse
import logging
import time
import warnings
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio import to_thread
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import Connection

from app.core.config.settings import get_settings
from app.db.alembic.revision_ids import LEGACY_MIGRATION_TO_NEW_REVISION, OLD_TO_NEW_REVISION_MAP, REVISION_ID_PATTERN
from app.db.migration_url import to_sync_database_url
from app.db.models import Base

logger = logging.getLogger(__name__)

_ALEMBIC_VERSION_TABLE = "alembic_version"
_ALEMBIC_VERSION_COLUMN = "version_num"
_LEGACY_MIGRATIONS_TABLE = "schema_migrations"
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
    "usage_history": frozenset({"idx_usage_window_account_latest", "idx_usage_window_account_time"}),
    "request_logs": frozenset(
        {
            "idx_logs_requested_at_id",
            "idx_logs_requested_at_model_tier",
            "idx_logs_model_effort_time",
            "idx_logs_status_error_time",
        }
    ),
    "api_keys": frozenset({"idx_api_keys_name"}),
}


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


class MigrationBootstrapError(RuntimeError):
    pass


def _script_location() -> str:
    return str((Path(__file__).resolve().parent / "alembic").resolve())


def _build_alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", _script_location())
    config.set_main_option("sqlalchemy.url", to_sync_database_url(database_url))
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
        connection.execute(
            text(
                " ".join(
                    (
                        f"CREATE TABLE {_ALEMBIC_VERSION_TABLE} (",
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
        current = _read_current_revision_from_connection(connection) if has_alembic else None

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


def _normalize_boolean_server_default(value: object) -> bool | None:
    candidate = value
    if hasattr(candidate, "arg"):
        candidate = getattr(candidate, "arg")
    if hasattr(candidate, "text"):
        candidate = getattr(candidate, "text")
    if isinstance(candidate, bool):
        return candidate
    rendered = str(candidate).strip().strip("()").strip("'\"").lower()
    if rendered in {"0", "false"}:
        return False
    if rendered in {"1", "true"}:
        return True
    return None


def _metadata_boolean_default(table_name: str, column_name: str) -> bool | None:
    table = Base.metadata.tables.get(table_name)
    if table is None:
        return None
    column = table.c.get(column_name)
    if column is None or column.server_default is None:
        return None
    try:
        python_type = column.type.python_type
    except Exception:
        return None
    if python_type is not bool:
        return None
    return _normalize_boolean_server_default(column.server_default)


def _reflected_boolean_default(connection: Connection, table_name: str, column_name: str) -> bool | None:
    inspector = inspect(connection)
    if not inspector.has_table(table_name):
        return None
    for column in inspector.get_columns(table_name):
        if column.get("name") != column_name:
            continue
        column_type = column.get("type")
        try:
            python_type = column_type.python_type if column_type is not None else None
        except Exception:
            python_type = None
        if python_type is not bool:
            return None
        return _normalize_boolean_server_default(column.get("default"))
    return None


def _is_semantic_boolean_default_drift(diff: object, connection: Connection) -> bool:
    if not isinstance(diff, tuple) or len(diff) < 7 or diff[0] != "modify_default":
        return False
    table_name = diff[2]
    column_name = diff[3]
    if not isinstance(table_name, str) or not isinstance(column_name, str):
        return False
    reflected_default = _reflected_boolean_default(connection, table_name, column_name)
    metadata_default = _metadata_boolean_default(table_name, column_name)
    return reflected_default is not None and metadata_default is not None and reflected_default == metadata_default


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
            raw_diffs = compare_metadata(migration_context, Base.metadata)
            diffs = [diff for diff in raw_diffs if not _is_semantic_boolean_default_drift(diff, connection)]
        manual_diffs = _manual_schema_drift_diffs(connection)

    return tuple(repr(diff) for diff in diffs) + manual_diffs


def run_upgrade(
    database_url: str,
    revision: str = "head",
    *,
    bootstrap_legacy: bool,
    auto_remap_legacy_revisions: bool = True,
) -> MigrationRunResult:
    config = _build_alembic_config(database_url)

    bootstrap_result = LegacyBootstrapResult(
        stamped_revision=None,
        legacy_row_count=0,
        unknown_migrations=(),
        had_non_contiguous_entries=False,
    )

    if bootstrap_legacy:
        bootstrap_result = _bootstrap_legacy_history(config)

    _ensure_alembic_version_table_capacity(config)
    if auto_remap_legacy_revisions:
        _remap_legacy_alembic_revisions(config)
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


def stamp_revision(database_url: str, revision: str) -> None:
    config = _build_alembic_config(database_url)
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Database migration utility for codex-lb.")
    parser.add_argument(
        "--db-url",
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
    database_url = args.db_url or get_settings().database_url

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
