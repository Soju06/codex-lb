"""Join dashboard-user and retry-claim migration histories."""

revision = "20260910_200000_merge_users_retry_claim_heads"
down_revision = (
    "20260910_170000_merge_guest_retry_claim_heads",
    "20260909_030000_add_audit_actor_columns",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent schemas are already applied."""


def downgrade() -> None:
    """Restore both parent stamps without changing their schemas or rows."""
