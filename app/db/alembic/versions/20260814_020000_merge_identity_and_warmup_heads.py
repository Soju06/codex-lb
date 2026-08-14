"""merge identity-index and quota-warmup migration heads

Revision ID: 20260814_020000_merge_identity_and_warmup_heads
Revises:
- 20260814_000000_add_accounts_chatgpt_identity_index
- 20260806_140000_merge_quota_warmup_claim_expiry_head
Create Date: 2026-08-14 20:00:00.000000

The accounts ChatGPT-identity index revision (#1732) and the quota warmup
claim-expiry lineage (#1646) branch from the same ancestor. Both sides are
additive; this no-op merge records the convergence so startup and deploy
preflight see one canonical Alembic head once both are present.
"""

from __future__ import annotations

revision = "20260814_020000_merge_identity_and_warmup_heads"
down_revision = (
    "20260814_000000_add_accounts_chatgpt_identity_index",
    "20260806_140000_merge_quota_warmup_claim_expiry_head",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
