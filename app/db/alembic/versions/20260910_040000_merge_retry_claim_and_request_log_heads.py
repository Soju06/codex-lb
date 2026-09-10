"""Join retry-claim receipts with request-log indexes."""

revision = "20260910_040000_merge_retry_claim_and_request_log_heads"
down_revision = (
    "20260829_000000_add_retry_circuit_admission_claim_marker",
    "20260910_000000_request_logs_missing_cost_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent schemas are already applied."""


def downgrade() -> None:
    """Restore the two parent heads without changing their schemas."""
