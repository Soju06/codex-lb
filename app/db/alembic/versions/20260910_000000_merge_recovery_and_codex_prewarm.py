"""Join recovery and dashboard Codex prewarm histories without schema changes."""

from __future__ import annotations

revision = "20260910_000000_merge_recovery_and_codex_prewarm"
down_revision = (
    "20260909_080000_merge_recovery_and_automation_budget",
    "20260909_100000_dashboard_codex_prewarm",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge parent version stamps while retaining schemas and data."""


def downgrade() -> None:
    """Restore parent version stamps without changing schemas or rows."""
