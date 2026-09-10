"""Join published CPA/spool and guest-session generation histories."""

from __future__ import annotations

revision = "20260910_030000_merge_cpa_guest_heads"
down_revision = (
    "20260910_020000_merge_cpa_spool_retention",
    "20260908_000000_add_guest_session_generation",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join stamps after both unchanged parent histories have completed."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without removing either schema or its data."""
    pass
