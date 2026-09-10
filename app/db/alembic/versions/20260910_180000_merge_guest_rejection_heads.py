"""Join guest-session revocation and rejection evidence history."""

revision = "20260910_180000_merge_guest_rejection_heads"
down_revision = (
    "20260910_160000_merge_rejection_spool_heads",
    "20260908_000000_add_guest_session_generation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
