"""Join guest-session generation and retry-claim migration histories."""

revision = "20260910_170000_merge_guest_retry_claim_heads"
down_revision = (
    "20260910_160000_merge_retry_claim_spool_heads",
    "20260908_000000_add_guest_session_generation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent schemas are already applied."""


def downgrade() -> None:
    """Restore both parent stamps without changing their schemas or rows."""
