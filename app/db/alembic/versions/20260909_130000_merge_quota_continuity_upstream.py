"""Join quota continuity recovery with current upstream settings history."""

from __future__ import annotations

revision = "20260909_130000_merge_quota_continuity_upstream"
down_revision = (
    "20260909_080000_merge_quota_failover_upstream",
    "20260909_120000_dashboard_conversation_archive",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
