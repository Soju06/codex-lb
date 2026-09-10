"""Join published CPA and dashboard invitation migration histories."""

from __future__ import annotations

revision = "20260910_050000_merge_cpa_dashboard_invites"
down_revision = (
    "20260910_040000_merge_cpa_dashboard_identity",
    "20260909_040000_add_dashboard_user_invites",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join stamps after both unchanged parent histories have completed."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without changing either schema or its data."""
    pass
