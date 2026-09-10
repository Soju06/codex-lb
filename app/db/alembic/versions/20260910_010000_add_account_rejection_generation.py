"""Persist the identity and request scope of account holds."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_010000_add_account_rejection_generation"
down_revision = "20260910_000000_request_logs_missing_cost_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    columns = (
        sa.Column("block_generation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("rejected_model", sa.String(), nullable=True),
        sa.Column("rejected_service_tier", sa.String(), nullable=True),
        sa.Column("probe_claim_token", sa.String(), nullable=True),
        sa.Column("probe_claim_expires_at", sa.DateTime(), nullable=True),
    )
    for column in columns:
        if column.name not in existing:
            op.add_column("accounts", column)


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("accounts")}
    for name in (
        "probe_claim_expires_at",
        "probe_claim_token",
        "rejected_service_tier",
        "rejected_model",
        "block_generation",
    ):
        if name in existing:
            op.drop_column("accounts", name)
