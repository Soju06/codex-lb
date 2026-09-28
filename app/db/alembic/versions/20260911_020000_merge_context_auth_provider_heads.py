"""Join the deployed context integration and authentication provider migrations."""

revision = "20260911_020000_merge_context_auth_provider_heads"
down_revision = (
    "20260910_180000_merge_context_dashboard_heads",
    "20260910_020000_add_dashboard_role_mappings",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
