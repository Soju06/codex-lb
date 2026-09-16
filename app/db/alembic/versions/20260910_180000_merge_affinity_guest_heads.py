"""Merge affinity observation and guest-session migration histories."""

revision = "20260910_180000_merge_affinity_guest_heads"
down_revision = (
    "20260910_143000_request_log_affinity",
    "20260908_000000_add_guest_session_generation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
