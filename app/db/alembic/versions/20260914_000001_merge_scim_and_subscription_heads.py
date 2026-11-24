"""Join the concurrent 2026-09-14 migration heads before transcript storage.

The SCIM-token and withdrawn-subscription-overflow revisions were both based
on the OIDC head and landed with the same timestamp prefix. This empty merge
revision makes their already-applied schema changes one upgrade path so the
transcript-core revision can safely follow both.
"""

revision = "20260914_000001_merge_scim_and_subscription_heads"
down_revision = (
    "20260914_000000_add_scim_tokens",
    "20260914_000000_drop_subscription_overflow_schema",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join the two parent revisions without changing their schema effects."""


def downgrade() -> None:
    """Re-expose both parent heads when the merge is reversed."""
