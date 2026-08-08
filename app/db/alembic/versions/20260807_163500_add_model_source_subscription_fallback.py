"""add model source subscription fallback

Revision ID: 20260807_163500_add_model_source_subscription_fallback
Revises: 20260806_020000_add_usage_history_bulk_covering_indexes
"""

import sqlalchemy as sa
from alembic import op

revision = "20260807_163500_add_model_source_subscription_fallback"
down_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_sources",
        sa.Column("is_subscription_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("model_sources", sa.Column("fallback_model", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("model_sources", "fallback_model")
    op.drop_column("model_sources", "is_subscription_fallback")
