"""Join recovery and request-log indexes without schema or data changes."""

from __future__ import annotations

revision = "20260910_030000_merge_recovery_and_request_log_indexes"
down_revision = (
    "20260910_020000_merge_recovery_and_round_three_settings",
    "20260910_010000_dashboard_spool_retention",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join both version histories while preserving their schema and rows."""


def downgrade() -> None:
    """Restore the parent stamps without mutating their schema or rows."""
