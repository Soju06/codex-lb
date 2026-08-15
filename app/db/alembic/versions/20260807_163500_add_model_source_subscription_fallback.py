"""add model source subscription fallback

Revision ID: 20260807_163500_add_model_source_subscription_fallback
Revises: 20260806_000000_add_anonymous_telemetry
"""

import sqlalchemy as sa
from alembic import op

revision = "20260807_163500_add_model_source_subscription_fallback"
down_revision = "20260806_000000_add_anonymous_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_sources",
        sa.Column("is_subscription_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("model_sources", sa.Column("fallback_model", sa.String(length=255), nullable=True))
    op.create_index(
        "uq_model_sources_subscription_fallback",
        "model_sources",
        ["is_subscription_fallback"],
        unique=True,
        postgresql_where=sa.text("is_subscription_fallback IS TRUE"),
        sqlite_where=sa.text("is_subscription_fallback = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_model_sources_subscription_fallback", table_name="model_sources")
    op.drop_column("model_sources", "fallback_model")
    op.drop_column("model_sources", "is_subscription_fallback")
