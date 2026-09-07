"""Add optional per-account 5h and weekly usage caps."""

import sqlalchemy as sa
from alembic import op

revision = "20260907_000000_add_account_usage_caps"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "usage_cap_5h_percent" not in columns:
        op.add_column("accounts", sa.Column("usage_cap_5h_percent", sa.Float(), nullable=True))
    if "usage_cap_weekly_percent" not in columns:
        op.add_column("accounts", sa.Column("usage_cap_weekly_percent", sa.Float(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    if "usage_cap_weekly_percent" in columns:
        op.drop_column("accounts", "usage_cap_weekly_percent")
    if "usage_cap_5h_percent" in columns:
        op.drop_column("accounts", "usage_cap_5h_percent")
