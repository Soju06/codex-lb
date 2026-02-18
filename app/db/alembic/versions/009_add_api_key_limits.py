"""add api_key_limits table and migrate legacy weekly limits

Revision ID: 009_add_api_key_limits
Revises: 008_add_api_keys
Create Date: 2026-02-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "009_add_api_key_limits"
down_revision = "008_add_api_keys"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "api_key_limits"):
        op.create_table(
            "api_key_limits",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "api_key_id",
                sa.String(),
                sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("limit_type", sa.String(), nullable=False),
            sa.Column("limit_window", sa.String(), nullable=False),
            sa.Column("max_value", sa.BigInteger(), nullable=False),
            sa.Column(
                "current_value",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("model_filter", sa.String(), nullable=True),
            sa.Column("reset_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "idx_api_key_limits_key_id",
            "api_key_limits",
            ["api_key_id"],
            unique=False,
        )

    # Migrate existing weekly_token_limit data to api_key_limits rows
    api_keys_cols = _columns(bind, "api_keys")
    if "weekly_token_limit" in api_keys_cols:
        api_keys = sa.table(
            "api_keys",
            sa.column("id", sa.String),
            sa.column("weekly_token_limit", sa.Integer),
            sa.column("weekly_tokens_used", sa.Integer),
            sa.column("weekly_reset_at", sa.DateTime),
        )
        api_key_limits = sa.table(
            "api_key_limits",
            sa.column("api_key_id", sa.String),
            sa.column("limit_type", sa.String),
            sa.column("limit_window", sa.String),
            sa.column("max_value", sa.BigInteger),
            sa.column("current_value", sa.BigInteger),
            sa.column("model_filter", sa.String),
            sa.column("reset_at", sa.DateTime),
        )

        result = bind.execute(
            sa.select(
                api_keys.c.id,
                api_keys.c.weekly_token_limit,
                api_keys.c.weekly_tokens_used,
                api_keys.c.weekly_reset_at,
            ).where(api_keys.c.weekly_token_limit.isnot(None))
        )

        for row in result:
            bind.execute(
                api_key_limits.insert().values(
                    api_key_id=row.id,
                    limit_type="total_tokens",
                    limit_window="weekly",
                    max_value=row.weekly_token_limit,
                    current_value=row.weekly_tokens_used,
                    model_filter=None,
                    reset_at=row.weekly_reset_at,
                )
            )

        # Drop legacy columns (batch for SQLite compatibility)
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.drop_column("weekly_token_limit")
            batch_op.drop_column("weekly_tokens_used")
            batch_op.drop_column("weekly_reset_at")


def downgrade() -> None:
    bind = op.get_bind()

    # Re-add legacy columns
    api_keys_cols = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "weekly_token_limit" not in api_keys_cols:
            batch_op.add_column(sa.Column("weekly_token_limit", sa.Integer(), nullable=True))
        if "weekly_tokens_used" not in api_keys_cols:
            batch_op.add_column(
                sa.Column(
                    "weekly_tokens_used",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "weekly_reset_at" not in api_keys_cols:
            batch_op.add_column(
                sa.Column(
                    "weekly_reset_at",
                    sa.DateTime(),
                    nullable=True,
                )
            )

    if _table_exists(bind, "api_key_limits"):
        op.drop_index("idx_api_key_limits_key_id", table_name="api_key_limits")
        op.drop_table("api_key_limits")
