"""Merge affinity observation and dashboard identity migration histories."""

revision = "20260910_200000_merge_affinity_identity_heads"
down_revision = (
    "20260910_180000_merge_affinity_guest_heads",
    "20260909_030000_add_audit_actor_columns",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
