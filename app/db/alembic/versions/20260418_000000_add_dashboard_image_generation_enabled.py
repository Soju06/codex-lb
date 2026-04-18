"""add image generation enabled toggle to dashboard settings

Revision ID: 20260418_000000_add_dashboard_image_generation_enabled
Revises: 20260413_000000_add_accounts_blocked_at
Create Date: 2026-04-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260418_000000_add_dashboard_image_generation_enabled"
down_revision = "20260413_000000_add_accounts_blocked_at"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns or "image_generation_enabled" in columns:
        return

    op.add_column(
        "dashboard_settings",
        sa.Column(
            "image_generation_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if "image_generation_enabled" not in columns:
        return
    op.drop_column("dashboard_settings", "image_generation_enabled")
