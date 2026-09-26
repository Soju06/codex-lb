"""Persist direct Responses source ownership before delivery."""

import sqlalchemy as sa
from alembic import op

revision = "20260926_000000_add_source_ownership"
down_revision = "20260923_000000_add_new_account_warmup_setting"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_source_ownership",
        sa.Column("reference_key", sa.String(64), primary_key=True),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_model_source_ownership_expires_at", "model_source_ownership", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_model_source_ownership_expires_at", table_name="model_source_ownership")
    op.drop_table("model_source_ownership")
