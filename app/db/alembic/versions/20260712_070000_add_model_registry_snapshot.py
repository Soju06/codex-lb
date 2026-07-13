"""add model registry snapshot table

Revision ID: 20260712_070000_add_model_registry_snapshot
Revises: 20260711_030000_add_limit_warmup_idle_threshold
Create Date: 2026-07-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260712_070000_add_model_registry_snapshot"
down_revision = "20260711_030000_add_limit_warmup_idle_threshold"
branch_labels = None
depends_on = None

_TABLE_NAME = "model_registry_snapshot"


def _has_table(table_name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    if _has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leader_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    if not _has_table(_TABLE_NAME):
        return
    op.drop_table(_TABLE_NAME)
