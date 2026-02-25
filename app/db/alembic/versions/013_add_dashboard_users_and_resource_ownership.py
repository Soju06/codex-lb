"""add dashboard users and resource ownership columns

Revision ID: 013_add_dashboard_users_and_resource_ownership
Revises: 012_add_import_without_overwrite_and_drop_accounts_email_unique
Create Date: 2026-02-25
"""

from __future__ import annotations

import os

import bcrypt
import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "013_add_dashboard_users_and_resource_ownership"
down_revision = "012_add_import_without_overwrite_and_drop_accounts_email_unique"
branch_labels = None
depends_on = None

_DEFAULT_ADMIN_ID = "dashboard-user-admin-default"
_DEFAULT_ADMIN_USERNAME = "admin"
_BOOTSTRAP_ADMIN_PASSWORD_ENV = "CODEX_LB_BOOTSTRAP_ADMIN_PASSWORD"


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _index_exists(connection: Connection, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    indexes = inspector.get_indexes(table_name)
    return any(str(idx.get("name")) == index_name for idx in indexes)


def _bootstrap_admin_password() -> str:
    value = os.getenv(_BOOTSTRAP_ADMIN_PASSWORD_ENV)
    if value is None:
        raise RuntimeError(
            f"{_BOOTSTRAP_ADMIN_PASSWORD_ENV} must be set before running migration {revision}",
        )
    normalized = value.strip()
    if len(normalized) < 8:
        raise RuntimeError(f"{_BOOTSTRAP_ADMIN_PASSWORD_ENV} must be at least 8 characters")
    return normalized


def _ensure_dashboard_user_role_type(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        return
    role_enum = sa.Enum(
        "admin",
        "user",
        name="dashboard_user_role",
        validate_strings=True,
    )
    role_enum.create(connection, checkfirst=True)


def _create_dashboard_users_table(connection: Connection) -> None:
    if _table_exists(connection, "dashboard_users"):
        return

    role_type = sa.Enum(
        "admin",
        "user",
        name="dashboard_user_role",
        validate_strings=True,
        create_type=False,
    )

    op.create_table(
        "dashboard_users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", role_type, nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("username", name="uq_dashboard_users_username"),
    )


def _ensure_owner_columns(connection: Connection) -> None:
    account_columns = _columns(connection, "accounts")
    if account_columns and "owner_user_id" not in account_columns:
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.add_column(sa.Column("owner_user_id", sa.String(), nullable=True))

    api_key_columns = _columns(connection, "api_keys")
    if api_key_columns and "owner_user_id" not in api_key_columns:
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.add_column(sa.Column("owner_user_id", sa.String(), nullable=True))


def _ensure_owner_indexes(connection: Connection) -> None:
    if _table_exists(connection, "accounts") and not _index_exists(connection, "accounts", "idx_accounts_owner_user_id"):
        op.create_index("idx_accounts_owner_user_id", "accounts", ["owner_user_id"], unique=False)
    if _table_exists(connection, "api_keys") and not _index_exists(connection, "api_keys", "idx_api_keys_owner_user_id"):
        op.create_index("idx_api_keys_owner_user_id", "api_keys", ["owner_user_id"], unique=False)
    if _table_exists(connection, "dashboard_users") and not _index_exists(
        connection, "dashboard_users", "idx_dashboard_users_username"
    ):
        op.create_index("idx_dashboard_users_username", "dashboard_users", ["username"], unique=True)


def _ensure_default_admin(connection: Connection) -> str:
    if not _table_exists(connection, "dashboard_users"):
        raise RuntimeError("dashboard_users table must exist before admin bootstrap")

    existing = connection.execute(
        sa.text("SELECT id FROM dashboard_users WHERE username = :username LIMIT 1"),
        {"username": _DEFAULT_ADMIN_USERNAME},
    ).first()
    if existing is not None and existing[0]:
        return str(existing[0])

    bootstrap_password = _bootstrap_admin_password()
    hashed_password = bcrypt.hashpw(bootstrap_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    connection.execute(
        sa.text(
            """
            INSERT INTO dashboard_users (id, username, password_hash, role, is_active)
            VALUES (:id, :username, :password_hash, :role, :is_active)
            """
        ),
        {
            "id": _DEFAULT_ADMIN_ID,
            "username": _DEFAULT_ADMIN_USERNAME,
            "password_hash": hashed_password,
            "role": "admin",
            "is_active": True,
        },
    )
    return _DEFAULT_ADMIN_ID


def _backfill_owner_columns(connection: Connection, admin_user_id: str) -> None:
    if _table_exists(connection, "accounts") and "owner_user_id" in _columns(connection, "accounts"):
        connection.execute(
            sa.text("UPDATE accounts SET owner_user_id = :owner_user_id WHERE owner_user_id IS NULL"),
            {"owner_user_id": admin_user_id},
        )
    if _table_exists(connection, "api_keys") and "owner_user_id" in _columns(connection, "api_keys"):
        connection.execute(
            sa.text("UPDATE api_keys SET owner_user_id = :owner_user_id WHERE owner_user_id IS NULL"),
            {"owner_user_id": admin_user_id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_dashboard_user_role_type(bind)
    _create_dashboard_users_table(bind)
    _ensure_owner_columns(bind)
    _ensure_owner_indexes(bind)
    admin_user_id = _ensure_default_admin(bind)
    _backfill_owner_columns(bind, admin_user_id)


def downgrade() -> None:
    return
