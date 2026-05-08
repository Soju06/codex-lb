"""add api key peer fallback urls

Revision ID: 20260430_000000_add_api_key_peer_fallback_urls
Revises: 20260428_000000_add_peer_fallback_targets
Create Date: 2026-04-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260430_000000_add_api_key_peer_fallback_urls"
down_revision = "20260428_000000_add_peer_fallback_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("api_key_peer_fallback_urls"):
        return

    op.create_table(
        "api_key_peer_fallback_urls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("api_key_id", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("api_key_id", "base_url", name="uq_api_key_peer_fallback_urls_key_base_url"),
    )
    op.create_index(
        "idx_api_key_peer_fallback_urls_key_priority",
        "api_key_peer_fallback_urls",
        ["api_key_id", "priority"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("api_key_peer_fallback_urls"):
        return
    indexes = {index["name"] for index in inspector.get_indexes("api_key_peer_fallback_urls")}
    if "idx_api_key_peer_fallback_urls_key_priority" in indexes:
        op.drop_index(
            "idx_api_key_peer_fallback_urls_key_priority",
            table_name="api_key_peer_fallback_urls",
        )
    op.drop_table("api_key_peer_fallback_urls")
