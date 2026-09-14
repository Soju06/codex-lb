"""Merge published account-bundle and OIDC migration heads without rewriting history."""

revision = "20260914_010000_merge_account_bundle_and_oidc_heads"
down_revision = (
    "20260912_020000_merge_account_bundle_and_dashboard_credentials_heads",
    "20260913_000000_add_oidc_provider_flow",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
