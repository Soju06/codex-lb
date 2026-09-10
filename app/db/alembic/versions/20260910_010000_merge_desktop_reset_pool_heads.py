"""Merge Desktop reset pooling with the main migration history."""

from __future__ import annotations

revision = "20260910_010000_merge_desktop_reset_pool_heads"
down_revision = (
    "20260909_210000_desktop_reset_pool",
    "20260910_000000_request_logs_missing_cost_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both completed histories without changing their data."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without removing schema or bindings."""
    pass
