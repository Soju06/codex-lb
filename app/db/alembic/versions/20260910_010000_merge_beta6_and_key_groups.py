"""Join beta.6 report history and deployed fork API-key groups."""

from __future__ import annotations

revision = "20260910_010000_merge_beta6_and_key_groups"
down_revision = (
    "20260909_060000_add_report_rollup",
    "20260910_000000_add_api_key_usage_group",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Both parent schemas are additive and already complete."""
    pass


def downgrade() -> None:
    """Restore the two parent stamps without removing their data."""
    pass
