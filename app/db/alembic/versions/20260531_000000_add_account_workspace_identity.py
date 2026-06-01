"""add account workspace identity metadata

Revision ID: 20260531_000000_add_account_workspace_identity
Revises: 20260601_000000_merge_relative_availability_and_usage_raw_heads
Create Date: 2026-05-31 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260531_000000_add_account_workspace_identity"
down_revision = "20260601_000000_merge_relative_availability_and_usage_raw_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_columns = {column["name"] for column in inspect(op.get_bind()).get_columns("accounts")}
    with op.batch_alter_table("accounts") as batch_op:
        if "workspace_id" not in existing_columns:
            batch_op.add_column(sa.Column("workspace_id", sa.String(), nullable=True))
        if "workspace_label" not in existing_columns:
            batch_op.add_column(sa.Column("workspace_label", sa.String(), nullable=True))
        if "seat_type" not in existing_columns:
            batch_op.add_column(sa.Column("seat_type", sa.String(), nullable=True))


def downgrade() -> None:
    existing_columns = {column["name"] for column in inspect(op.get_bind()).get_columns("accounts")}
    with op.batch_alter_table("accounts") as batch_op:
        if "seat_type" in existing_columns:
            batch_op.drop_column("seat_type")
        if "workspace_label" in existing_columns:
            batch_op.drop_column("workspace_label")
        if "workspace_id" in existing_columns:
            batch_op.drop_column("workspace_id")
