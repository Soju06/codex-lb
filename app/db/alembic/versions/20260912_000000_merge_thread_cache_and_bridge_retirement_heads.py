"""Merge the thread-cache identity and bridge owner-retirement histories.

Both revisions chained onto ``20260911_030000_add_local_login_policy`` and
merged within minutes of each other, forking the graph. Neither is wrong on
its own, so converge them rather than re-chaining either.
"""

revision = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
down_revision = (
    "20260911_040000_add_thread_cache_identity_mode",
    "20260911_060000_add_bridge_session_continuity_abandonment",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
