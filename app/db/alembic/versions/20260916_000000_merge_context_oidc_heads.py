"""Join deployed context ownership with the current upstream OIDC migrations."""

revision = "20260916_000000_merge_context_oidc_heads"
down_revision = (
    "20260911_020000_merge_context_auth_provider_heads",
    "20260913_000000_add_oidc_provider_flow",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
