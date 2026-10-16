"""Make automatic warm-up enrollment configurable for future accounts."""

import sqlalchemy as sa
from alembic import op

revision = "20261003_000000_add_new_account_warmup_setting"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "dashboard_settings",
        sa.Column("limit_warmup_auto_enable_new_accounts", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("dashboard_settings", "limit_warmup_auto_enable_new_accounts")
