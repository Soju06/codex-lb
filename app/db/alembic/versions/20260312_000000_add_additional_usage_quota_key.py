"""add canonical quota_key to additional_usage_history

Revision ID: 20260312_000000_add_additional_usage_quota_key
Revises: 20260310_000000_fix_postgresql_enum_value_casing
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

from app.modules.usage.additional_quota_keys import canonicalize_additional_quota_key

# revision identifiers, used by Alembic.
revision = "20260312_000000_add_additional_usage_quota_key"
down_revision = "20260310_000000_fix_postgresql_enum_value_casing"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


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


def _canonical_quota_key(limit_name: str | None, metered_feature: str | None) -> str:
    return (
        canonicalize_additional_quota_key(
            limit_name=limit_name,
            metered_feature=metered_feature,
        )
        or "unknown"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "additional_usage_history"):
        return

    existing_columns = _columns(bind, "additional_usage_history")
    if "quota_key" not in existing_columns:
        with op.batch_alter_table("additional_usage_history") as batch_op:
            batch_op.add_column(sa.Column("quota_key", sa.String(), nullable=False, server_default=""))

    table = sa.table(
        "additional_usage_history",
        sa.column("id", sa.Integer()),
        sa.column("limit_name", sa.String()),
        sa.column("metered_feature", sa.String()),
        sa.column("quota_key", sa.String()),
    )
    rows = bind.execute(
        sa.select(
            table.c.id,
            table.c.limit_name,
            table.c.metered_feature,
        )
    ).fetchall()
    for row in rows:
        bind.execute(
            table.update()
            .where(table.c.id == row.id)
            .values(quota_key=_canonical_quota_key(row.limit_name, row.metered_feature))
        )

    existing_indexes = _indexes(bind, "additional_usage_history")
    if "ix_additional_usage_limit_window" in existing_indexes:
        op.drop_index("ix_additional_usage_limit_window", table_name="additional_usage_history")
    if "ix_additional_usage_quota_window" in existing_indexes:
        op.drop_index("ix_additional_usage_quota_window", table_name="additional_usage_history")
    if "ix_additional_usage_history_composite" in existing_indexes:
        op.drop_index("ix_additional_usage_history_composite", table_name="additional_usage_history")

    op.create_index(
        "ix_additional_usage_quota_window",
        "additional_usage_history",
        ["quota_key", "window", "account_id", "recorded_at"],
    )
    op.create_index(
        "ix_additional_usage_history_composite",
        "additional_usage_history",
        ["account_id", "quota_key", "window", "recorded_at"],
    )

    with op.batch_alter_table("additional_usage_history") as batch_op:
        batch_op.alter_column(
            "quota_key",
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=None,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "additional_usage_history"):
        return
    existing_indexes = _indexes(bind, "additional_usage_history")
    if "ix_additional_usage_quota_window" in existing_indexes:
        op.drop_index("ix_additional_usage_quota_window", table_name="additional_usage_history")
    if "ix_additional_usage_history_composite" in existing_indexes:
        op.drop_index("ix_additional_usage_history_composite", table_name="additional_usage_history")
    op.create_index(
        "ix_additional_usage_limit_window",
        "additional_usage_history",
        ["limit_name", "window", "account_id", "recorded_at"],
    )
    op.create_index(
        "ix_additional_usage_history_composite",
        "additional_usage_history",
        ["account_id", "limit_name", "window", "recorded_at"],
    )
    existing_columns = _columns(bind, "additional_usage_history")
    if "quota_key" in existing_columns:
        with op.batch_alter_table("additional_usage_history") as batch_op:
            batch_op.drop_column("quota_key")
