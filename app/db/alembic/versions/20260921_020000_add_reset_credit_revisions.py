"""Account-scoped invalidation without losing compatibility with older replicas."""

import sqlalchemy as sa
from alembic import op

revision = "20260921_020000_add_reset_credit_revisions"
down_revision = "20260921_010000_add_reset_credit_outcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reset_credit_snapshot_revisions",
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("reset_credit_snapshot_revisions")
