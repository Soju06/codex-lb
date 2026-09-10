"""Join recovery and round-three dashboard settings without schema mutations."""

from __future__ import annotations

revision = "20260910_020000_merge_recovery_and_round_three_settings"
down_revision = (
    "20260910_010000_merge_recovery_and_request_budgets",
    "20260909_120000_dashboard_conversation_archive",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge both version stamps without modifying either parent's data."""


def downgrade() -> None:
    """Restore both parent stamps while retaining their schemas and rows."""
