"""Add optional usage-sharing groups to API keys."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_000000_add_api_key_usage_group"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("usage_group", sa.String(128), nullable=True))
    op.create_index("ix_api_keys_usage_group", "api_keys", ["usage_group"])


def downgrade() -> None:
    op.drop_index("ix_api_keys_usage_group", table_name="api_keys")
    op.drop_column("api_keys", "usage_group")
