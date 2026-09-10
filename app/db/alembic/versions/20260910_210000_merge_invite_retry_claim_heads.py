"""Join dashboard-invite and retry-claim migration histories."""

revision = "20260910_210000_merge_invite_retry_claim_heads"
down_revision = (
    "20260910_200000_merge_users_retry_claim_heads",
    "20260909_040000_add_dashboard_user_invites",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent schemas are already applied."""


def downgrade() -> None:
    """Restore both parent stamps without changing their schemas or rows."""
