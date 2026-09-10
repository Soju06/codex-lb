"""Join Desktop reset and dashboard spool-retention migration histories."""

from __future__ import annotations

revision = "20260910_020000_merge_reset_spool_heads"
down_revision = (
    "20260910_010000_merge_desktop_reset_pool_heads",
    "20260910_010000_dashboard_spool_retention",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join existing schemas without changing their data."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without removing their schemas."""
    pass
