"""Join the independently merged SCIM and overflow-removal histories."""

revision = "20260918_000000_merge_scim_and_overflow_removal"
down_revision = (
    "20260914_000000_add_scim_tokens",
    "20260914_000000_drop_subscription_overflow_schema",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
