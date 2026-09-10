"""Join retry-claim receipts and spool-retention migration histories."""

revision = "20260910_160000_merge_retry_claim_spool_heads"
down_revision = (
    "20260910_040000_merge_retry_claim_and_request_log_heads",
    "20260910_010000_dashboard_spool_retention",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent schemas are already applied."""


def downgrade() -> None:
    """Restore both parent stamps without changing their schemas or rows."""
