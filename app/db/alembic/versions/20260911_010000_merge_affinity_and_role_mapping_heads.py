"""Merge the affinity-invite and role-mapping migration histories."""

revision = "20260911_010000_merge_affinity_and_role_mapping_heads"
down_revision = (
    "20260910_220000_merge_affinity_invite_heads",
    "20260911_000000_model_source_pins_kind_expires_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
