"""Merge published affinity identity and dashboard invite histories."""

revision = "20260910_220000_merge_affinity_invite_heads"
down_revision = (
    "20260910_200000_merge_affinity_identity_heads",
    "20260909_040000_add_dashboard_user_invites",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
