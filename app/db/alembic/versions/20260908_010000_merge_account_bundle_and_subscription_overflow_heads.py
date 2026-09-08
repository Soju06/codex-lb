"""Merge account-bundle and subscription-overflow migration lineages.

Preserve both previously published revision identities while providing one
upgrade head for existing installations from either branch.
"""

from __future__ import annotations

revision = "20260908_010000_merge_account_bundle_and_subscription_overflow_heads"
down_revision = (
    "20260830_010000_merge_accounts_email_index_and_quota_warmup_heads",
    "20260908_000000_add_subscription_overflow",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
