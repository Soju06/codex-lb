"""merge the recovery-dispatch and hourly-rollup migration heads.

The durable HTTP bridge recovery branch and upstream's cancelled-count rollup
landed as independent additive revisions.  This no-op merge keeps startup and
CI migration checks at one canonical Alembic head without rewriting either
already-applied lineage.
"""

from __future__ import annotations

revision = "20260812_000000_merge_recovery_dispatch_and_hourly_cancelled_heads"
down_revision = (
    "20260810_000000_add_http_bridge_recovery_dispatch_count",
    "20260811_000000_add_hourly_rollup_cancelled_count",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
