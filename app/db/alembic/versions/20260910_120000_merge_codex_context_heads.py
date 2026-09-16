"""Join deployed context ownership with current upstream migrations."""

revision = "20260910_120000_merge_codex_context_heads"
down_revision = (
    "20260905_120000_add_codex_context_ownership",
    "20260910_000000_request_logs_missing_cost_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
