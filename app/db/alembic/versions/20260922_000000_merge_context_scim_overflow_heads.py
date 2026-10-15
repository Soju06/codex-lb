"""Join deployed context ownership with the published SCIM and overflow withdrawal heads."""

revision = "20260922_000000_merge_context_scim_overflow_heads"
down_revision = (
    "20260916_000000_merge_context_oidc_heads",
    "20260914_000000_add_scim_tokens",
    "20260914_000000_drop_subscription_overflow_schema",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
