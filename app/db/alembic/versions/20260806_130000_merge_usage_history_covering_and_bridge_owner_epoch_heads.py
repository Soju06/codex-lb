"""merge usage-history covering index and HTTP bridge owner epoch heads

Revision ID: 20260806_130000_merge_usage_history_covering_and_bridge_owner_epoch_heads
Revises:
- 20260806_020000_add_usage_history_bulk_covering_indexes
- 20260806_120000_add_http_bridge_owner_process_epoch
Create Date: 2026-08-06 13:00:00.000000

Both migrations are additive, revise
``20260730_000000_add_api_key_fair_share_threshold``, and were introduced from
independent branches. The bridge owner-epoch revision already shipped, so its
id and parent must not move; this no-op merge records the convergence instead
so startup and the deploy preflight see one canonical Alembic head.
"""

from __future__ import annotations

revision = "20260806_130000_merge_usage_history_covering_and_bridge_owner_epoch_heads"
down_revision = (
    "20260806_020000_add_usage_history_bulk_covering_indexes",
    "20260806_120000_add_http_bridge_owner_process_epoch",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
