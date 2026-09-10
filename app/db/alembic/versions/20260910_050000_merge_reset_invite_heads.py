"""Join reset pooling and dashboard invitations without rewriting history."""

from __future__ import annotations

revision = "20260910_050000_merge_reset_invite_heads"
down_revision = (
    "20260910_040000_merge_reset_dashboard_user_heads",
    "20260909_040000_add_dashboard_user_invites",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Retain both parent schemas and stored data."""
    pass


def downgrade() -> None:
    """Restore the direct parent stamps without deleting data."""
    pass
