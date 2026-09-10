"""Join recovery and dashboard request-budget histories without schema changes."""

from __future__ import annotations

revision = "20260910_010000_merge_recovery_and_request_budgets"
down_revision = (
    "20260910_000000_merge_recovery_and_codex_prewarm",
    "20260909_080000_dashboard_stream_bridge_budgets",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge parent version stamps while retaining schemas and data."""


def downgrade() -> None:
    """Restore parent version stamps without changing schemas or rows."""
