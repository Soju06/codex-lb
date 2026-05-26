"""add reset-confirmed limit warm-up trigger

Revision ID: 20260522_000000_add_limit_warmup_trigger
Revises: 20260520_010000_add_request_logs_api_key_account_index
Create Date: 2026-05-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260522_000000_add_limit_warmup_trigger"
down_revision = "20260520_010000_add_request_logs_api_key_account_index"
branch_labels = None
depends_on = None

_WARMUP_TABLE = "account_limit_warmups"


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _add_column_if_missing(
    connection: Connection,
    table_name: str,
    column_name: str,
    column: sa.Column,
) -> None:
    columns = _columns(connection, table_name)
    if not columns or column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(column)


def _drop_column_if_present(connection: Connection, table_name: str, column_name: str) -> None:
    columns = _columns(connection, table_name)
    if not columns or column_name not in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_column(column_name)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("accounts"):
        _add_column_if_missing(
            bind,
            "accounts",
            "limit_warmup_enabled",
            sa.Column("limit_warmup_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if inspector.has_table("request_logs"):
        _add_column_if_missing(
            bind,
            "request_logs",
            "source",
            sa.Column("source", sa.String(), nullable=True),
        )
        if "idx_logs_source_requested_at" not in _indexes(bind, "request_logs"):
            op.create_index(
                "idx_logs_source_requested_at",
                "request_logs",
                ["source", sa.text("requested_at DESC")],
                unique=False,
            )

    if inspector.has_table("dashboard_settings"):
        dashboard_columns = {
            "limit_warmup_enabled": sa.Column(
                "limit_warmup_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            "limit_warmup_windows": sa.Column(
                "limit_warmup_windows",
                sa.String(),
                nullable=False,
                server_default=sa.text("'both'"),
            ),
            "limit_warmup_model": sa.Column(
                "limit_warmup_model",
                sa.String(),
                nullable=False,
                server_default=sa.text("'auto'"),
            ),
            "limit_warmup_prompt": sa.Column(
                "limit_warmup_prompt",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'Say OK.'"),
            ),
            "limit_warmup_cooldown_seconds": sa.Column(
                "limit_warmup_cooldown_seconds",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("3600"),
            ),
            "limit_warmup_min_available_percent": sa.Column(
                "limit_warmup_min_available_percent",
                sa.Float(),
                nullable=False,
                server_default=sa.text("100.0"),
            ),
        }
        for column_name, column in dashboard_columns.items():
            _add_column_if_missing(bind, "dashboard_settings", column_name, column)

    if not inspector.has_table(_WARMUP_TABLE):
        op.create_table(
            _WARMUP_TABLE,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("window", sa.String(), nullable=False),
            sa.Column("reset_at", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("attempted_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint(
                "account_id",
                "window",
                "reset_at",
                name="uq_account_limit_warmups_account_window_reset",
            ),
        )

    warmup_indexes = _indexes(bind, _WARMUP_TABLE)
    if "idx_account_limit_warmups_account_attempted" not in warmup_indexes:
        op.create_index(
            "idx_account_limit_warmups_account_attempted",
            _WARMUP_TABLE,
            ["account_id", sa.text("attempted_at DESC")],
            unique=False,
        )
    if "idx_account_limit_warmups_status_attempted" not in warmup_indexes:
        op.create_index(
            "idx_account_limit_warmups_status_attempted",
            _WARMUP_TABLE,
            ["status", sa.text("attempted_at DESC")],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table(_WARMUP_TABLE):
        warmup_indexes = _indexes(bind, _WARMUP_TABLE)
        if "idx_account_limit_warmups_status_attempted" in warmup_indexes:
            op.drop_index("idx_account_limit_warmups_status_attempted", table_name=_WARMUP_TABLE)
        if "idx_account_limit_warmups_account_attempted" in warmup_indexes:
            op.drop_index("idx_account_limit_warmups_account_attempted", table_name=_WARMUP_TABLE)
        op.drop_table(_WARMUP_TABLE)

    if inspector.has_table("dashboard_settings"):
        for column_name in (
            "limit_warmup_min_available_percent",
            "limit_warmup_cooldown_seconds",
            "limit_warmup_prompt",
            "limit_warmup_model",
            "limit_warmup_windows",
            "limit_warmup_enabled",
        ):
            _drop_column_if_present(bind, "dashboard_settings", column_name)

    if inspector.has_table("request_logs"):
        if "idx_logs_source_requested_at" in _indexes(bind, "request_logs"):
            op.drop_index("idx_logs_source_requested_at", table_name="request_logs")
        _drop_column_if_present(bind, "request_logs", "source")

    if inspector.has_table("accounts"):
        _drop_column_if_present(bind, "accounts", "limit_warmup_enabled")
