"""Join the context integration and dashboard user migrations."""

revision = "20260910_180000_merge_context_dashboard_heads"
down_revision = (
    "20260910_120000_merge_codex_context_heads",
    "20260910_000000_add_totp_required_for_admin_role",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
