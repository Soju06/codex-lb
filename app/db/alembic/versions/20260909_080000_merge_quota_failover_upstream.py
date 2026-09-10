"""Join quota failover and upstream dashboard/automation migrations."""

from __future__ import annotations

revision = "20260909_080000_merge_quota_failover_upstream"
down_revision = (
    "20260908_030000_add_quota_failover_setting",
    "20260909_070000_automation_run_claim_budget",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two revision stamps without changing data."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without changing data."""
    pass
