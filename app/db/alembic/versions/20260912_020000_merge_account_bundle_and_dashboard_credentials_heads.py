"""Merge published account-bundle and dashboard-credential migration heads."""

revision = "20260912_020000_merge_account_bundle_and_dashboard_credentials_heads"
down_revision = (
    "20260908_010000_merge_account_bundle_and_subscription_overflow_heads",
    "20260912_010000_drop_legacy_dashboard_credentials",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
