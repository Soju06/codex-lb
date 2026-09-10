"""Join reset pooling and guest-session generation without rewriting history."""

from __future__ import annotations

revision = "20260910_030000_merge_reset_guest_heads"
down_revision = (
    "20260910_020000_merge_reset_spool_heads",
    "20260908_000000_add_guest_session_generation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join existing schemas while retaining stored authentication state."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without removing their schemas or data."""
    pass
