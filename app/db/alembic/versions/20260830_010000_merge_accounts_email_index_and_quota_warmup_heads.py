"""Merge the account-email index and quota-warmup claim-expiry heads.

The account bundle branch and main added independent schema revisions on top
of the same account-identity-index revision. This no-op merge keeps Alembic at
one canonical head without rewriting either already-applied lineage.
"""

from __future__ import annotations

revision = "20260830_010000_merge_accounts_email_index_and_quota_warmup_heads"
down_revision = (
    "20260828_010000_add_accounts_email_lower_index",
    "20260830_000000_add_quota_warmup_claim_expiry",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
