"""merge HTTP bridge recovery and capability lineage heads

Revision ID: 20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads
Revises:
- 20260730_000000_add_http_bridge_recovery_attempts
- 20260731_000000_add_capability_lineage_markers
Create Date: 2026-08-03

Both migrations are additive and were introduced from independent branches.
This no-op merge records their convergence so startup and migration checks see
one canonical Alembic head.
"""

from __future__ import annotations

revision = "20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads"
down_revision = (
    "20260730_000000_add_http_bridge_recovery_attempts",
    "20260731_000000_add_capability_lineage_markers",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
