"""Merge the released and SCIM-anchored overflow-retirement branches.

Revision ID: 20260914_000002_merge_overflow_retirement_heads
Revises: 20260914_000000_drop_subscription_overflow_schema,
         20260914_000001_drop_subscription_overflow_schema
Create Date: 2026-09-14

The first parent shipped on ``main`` before the parallel SCIM migration was
visible. It cannot be renamed or removed because deployed databases may carry
that stamp. The second parent keeps the guarded retirement ordered after SCIM.
Both drops are idempotent; this revision only records convergence.
"""

from __future__ import annotations

revision = "20260914_000002_merge_overflow_retirement_heads"
down_revision = (
    "20260914_000000_drop_subscription_overflow_schema",
    "20260914_000001_drop_subscription_overflow_schema",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
