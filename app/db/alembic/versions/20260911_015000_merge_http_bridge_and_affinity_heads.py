"""Merge the HTTP bridge request-log and affinity-history migration heads."""

revision = "20260911_015000_merge_http_bridge_and_affinity_heads"
down_revision = (
    "20260910_030000_merge_recovery_and_request_log_indexes",
    "20260911_010000_merge_pin_index_and_affinity_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
