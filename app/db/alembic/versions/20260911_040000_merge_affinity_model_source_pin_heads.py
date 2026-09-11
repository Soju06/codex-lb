"""Merge the request-log affinity and model-source-pin index histories.

#2352 and #2355 landed within minutes of each other and each was written
against a parent that was head at the time, so main ended with two alembic
heads. ``upgrade head`` then follows only one lineage and every index the
other lineage owns is reported missing. This joins them; it carries no
schema change of its own.
"""

revision = "20260911_040000_merge_affinity_model_source_pin_heads"
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
