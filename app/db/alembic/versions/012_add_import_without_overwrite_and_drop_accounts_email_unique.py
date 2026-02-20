"""add import_without_overwrite and drop accounts.email unique constraint

Revision ID: 012_add_import_without_overwrite_and_drop_accounts_email_unique
Revises: 011_add_api_key_usage_reservations
Create Date: 2026-02-18
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "012_add_import_without_overwrite_and_drop_accounts_email_unique"
down_revision = "011_add_api_key_usage_reservations"
branch_labels = None
depends_on = None

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ACCOUNTS_EMAIL_INDEX = "idx_accounts_email"


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    indexes = inspector.get_indexes(table_name)
    return any(idx["name"] == index_name for idx in indexes)


def _is_safe_identifier(name: str) -> bool:
    return bool(_IDENTIFIER_RE.fullmatch(name))


def _quote_identifier(name: str) -> str:
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _sqlite_has_email_unique_index(connection: Connection) -> bool:
    rows = connection.execute(sa.text("PRAGMA index_list(accounts)")).fetchall()
    for row in rows:
        if len(row) < 3:
            continue
        index_name = str(row[1])
        is_unique = bool(row[2])
        if not is_unique:
            continue

        escaped_name = index_name.replace('"', '""')
        index_columns = connection.execute(sa.text(f'PRAGMA index_info("{escaped_name}")')).fetchall()
        column_names = [str(info[2]) for info in index_columns if len(info) > 2]
        if column_names == ["email"]:
            return True
    return False


def _sqlite_referencing_tables(connection: Connection) -> list[str]:
    inspector = sa.inspect(connection)
    table_names = sorted(str(name) for name in inspector.get_table_names())
    referencing: list[str] = []
    for table_name in table_names:
        if table_name == "accounts":
            continue
        for foreign_key in inspector.get_foreign_keys(table_name):
            referred_table = foreign_key.get("referred_table")
            if str(referred_table) == "accounts":
                referencing.append(table_name)
                break
    return referencing


def _sqlite_create_accounts_copy_without_email_unique(connection: Connection) -> None:
    pragma_rows = connection.execute(sa.text("PRAGMA table_info(accounts)")).fetchall()
    if not pragma_rows:
        return

    primary_key_columns = [str(row[1]) for row in pragma_rows if len(row) > 5 and int(row[5]) > 0]
    pk_count = len(primary_key_columns)
    column_definitions: list[str] = []
    ordered_column_names: list[str] = []

    for row in pragma_rows:
        column_name = str(row[1])
        column_type = str(row[2]) if row[2] is not None else ""
        not_null = bool(row[3])
        default_value = row[4]
        is_pk = bool(row[5])

        definition_parts = [_quote_identifier(column_name)]
        if column_type:
            definition_parts.append(column_type)
        if not_null:
            definition_parts.append("NOT NULL")
        if default_value is not None:
            definition_parts.append(f"DEFAULT {default_value}")
        if is_pk and pk_count == 1:
            definition_parts.append("PRIMARY KEY")

        column_definitions.append(" ".join(definition_parts))
        ordered_column_names.append(_quote_identifier(column_name))

    if pk_count > 1:
        pk_columns = ", ".join(_quote_identifier(name) for name in primary_key_columns)
        column_definitions.append(f"PRIMARY KEY ({pk_columns})")

    create_sql = f"CREATE TABLE accounts_new ({', '.join(column_definitions)})"
    columns_csv = ", ".join(ordered_column_names)
    copy_sql = f"INSERT INTO accounts_new ({columns_csv}) SELECT {columns_csv} FROM accounts"

    connection.execute(sa.text(create_sql))
    connection.execute(sa.text(copy_sql))
    connection.execute(sa.text("DROP TABLE accounts"))
    connection.execute(sa.text("ALTER TABLE accounts_new RENAME TO accounts"))


def _sqlite_drop_accounts_email_unique(connection: Connection) -> None:
    if not _sqlite_has_email_unique_index(connection):
        return

    referencing_tables = _sqlite_referencing_tables(connection)
    backup_pairs: list[tuple[str, str]] = []

    for table_name in referencing_tables:
        if not _is_safe_identifier(table_name):
            continue
        backup_table = f"_backup_accounts_email_unique_{table_name}"
        if not _is_safe_identifier(backup_table):
            continue
        backup_pairs.append((table_name, backup_table))
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {backup_table}"))
        connection.execute(sa.text(f"CREATE TEMP TABLE {backup_table} AS SELECT * FROM {table_name}"))
        connection.execute(sa.text(f"DELETE FROM {table_name}"))

    _sqlite_create_accounts_copy_without_email_unique(connection)

    for table_name, backup_table in backup_pairs:
        connection.execute(sa.text(f"INSERT INTO {table_name} SELECT * FROM {backup_table}"))
        connection.execute(sa.text(f"DROP TABLE IF EXISTS {backup_table}"))


def _ensure_accounts_email_index(connection: Connection) -> None:
    if not _table_exists(connection, "accounts"):
        return
    if _index_exists(connection, _ACCOUNTS_EMAIL_INDEX, "accounts"):
        return
    op.create_index(_ACCOUNTS_EMAIL_INDEX, "accounts", ["email"], unique=False)


def upgrade() -> None:
    bind = op.get_bind()

    dashboard_columns = _columns(bind, "dashboard_settings")
    if dashboard_columns and "import_without_overwrite" not in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "import_without_overwrite",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    if not _table_exists(bind, "accounts"):
        return

    if bind.dialect.name == "sqlite":
        _sqlite_drop_accounts_email_unique(bind)
    elif bind.dialect.name == "postgresql":
        inspector = sa.inspect(bind)
        for unique_constraint in inspector.get_unique_constraints("accounts"):
            if unique_constraint.get("column_names") == ["email"] and unique_constraint.get("name"):
                op.drop_constraint(str(unique_constraint["name"]), "accounts", type_="unique")
                break
        else:
            op.execute(sa.text("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_email_key"))

    _ensure_accounts_email_index(bind)


def downgrade() -> None:
    # Re-introducing the email unique constraint can fail when duplicate rows exist.
    return
