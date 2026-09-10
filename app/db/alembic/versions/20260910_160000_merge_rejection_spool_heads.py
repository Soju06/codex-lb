"""Join rejection evidence and dashboard spool retention history."""

revision = "20260910_160000_merge_rejection_spool_heads"
down_revision = (
    "20260910_010000_add_account_rejection_generation",
    "20260910_010000_dashboard_spool_retention",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
