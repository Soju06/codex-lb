"""add api key account assignment generation

Revision ID: 20260727_000000_add_api_key_assignment_generation
Revises: 20260726_000000_merge_account_concurrency_overrides_and_main
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260727_000000_add_api_key_assignment_generation"
down_revision = "20260726_000000_merge_account_concurrency_overrides_and_main"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    existing_columns = _columns(bind, "api_keys")
    if not existing_columns:
        return

    with op.batch_alter_table("api_keys") as batch_op:
        if "account_assignment_generation" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "account_assignment_generation",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("1"),
                )
            )
        if "account_assignment_changed_at" not in existing_columns:
            batch_op.add_column(sa.Column("account_assignment_changed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing_columns = _columns(bind, "api_keys")
    if not existing_columns:
        return

    with op.batch_alter_table("api_keys") as batch_op:
        if "account_assignment_changed_at" in existing_columns:
            batch_op.drop_column("account_assignment_changed_at")
        if "account_assignment_generation" in existing_columns:
            batch_op.drop_column("account_assignment_generation")
