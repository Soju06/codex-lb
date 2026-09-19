"""Merge the model-source pin index and published affinity histories."""

revision = "20260911_010000_merge_pin_index_and_affinity_heads"
down_revision = (
    "20260911_000000_model_source_pins_kind_expires_index",
    "20260910_220000_merge_affinity_invite_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
