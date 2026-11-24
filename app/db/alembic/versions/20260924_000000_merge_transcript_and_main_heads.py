"""Converge the transcript and upstream SCIM/subscription migration heads.

The transcript branch retains its earlier merge revision for databases that may
already have applied it, while current ``main`` carries an equivalent merge
revision under a later id. This empty merge makes both published paths one
upgrade lineage after rebasing the branch onto ``main``.
"""

revision = "20260924_000000_merge_transcript_and_main_heads"
down_revision = (
    "20260914_010000_add_http_bridge_transcript_core",
    "20260918_000000_merge_scim_and_overflow_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
