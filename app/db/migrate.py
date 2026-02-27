from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio import to_thread
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection

from app.core.config.settings import get_settings
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

LEGACY_TO_REVISION: dict[str, str] = {migration_name: migration_name for migration_name in LEGACY_MIGRATION_ORDER}


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


def _read_table_names(connection: Connection) -> set[str]:
    inspector = inspect(connection)
    return set(inspector.get_table_names())


def _read_legacy_migration_names(connection: Connection) -> set[str]:
    result = connection.execute(text(f"SELECT name FROM {_LEGACY_MIGRATIONS_TABLE}"))
    names = {str(row[0]) for row in result.fetchall() if row and row[0] is not None}
    return names


def _read_current_revision_from_connection(connection: Connection) -> str | None:
    rows = connection.execute(text(f"SELECT {_ALEMBIC_VERSION_COLUMN} FROM {_ALEMBIC_VERSION_TABLE}")).fetchall()
    revisions = [str(row[0]) for row in rows if row and row[0]]
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

    with create_engine(sync_database_url, future=True).connect() as connection:
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
    with create_engine(sync_database_url, future=True).connect() as connection:
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
    with create_engine(sync_database_url, future=True).begin() as connection:
        _ensure_alembic_version_table_capacity_for_connection(connection, required_length=required_length)


def inspect_migration_state(database_url: str) -> MigrationState:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)
    head_revision = _head_revision(config)

    with create_engine(sync_database_url, future=True).connect() as connection:
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


def check_schema_drift(database_url: str) -> tuple[str, ...]:
    config = _build_alembic_config(database_url)
    sync_database_url = _required_sqlalchemy_url(config)

    with create_engine(sync_database_url, future=True).connect() as connection:
        migration_context = MigrationContext.configure(
            connection=connection,
            opts={
                "target_metadata": Base.metadata,
                "compare_type": True,
                "compare_server_default": True,
            },
        )
        diffs = compare_metadata(migration_context, Base.metadata)

    return tuple(repr(diff) for diff in diffs)


def run_upgrade(
    database_url: str,
    revision: str = "head",
    *,
    bootstrap_legacy: bool,
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
    return await to_thread.run_sync(
        lambda: run_upgrade(database_url, "head", bootstrap_legacy=True),
    )


def current_revision(database_url: str) -> str | None:
    state = inspect_migration_state(database_url)
    return state.current_revision


def stamp_revision(database_url: str, revision: str) -> None:
    config = _build_alembic_config(database_url)
    _ensure_alembic_version_table_capacity(config)
    command.stamp(config, revision)


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

    subparsers.add_parser("current", help="Print current alembic revision.")

    subparsers.add_parser("check", help="Check model/schema drift against current database schema.")

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
        drift = check_schema_drift(database_url)
        if drift:
            print("schema_drift_detected")
            for diff in drift:
                print(diff)
            raise SystemExit(1)
        print("schema_drift=none")
        return

    if args.command == "stamp":
        stamp_revision(database_url, args.revision)
        print(f"stamped={args.revision}")
        return

    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
