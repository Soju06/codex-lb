"""merge the durable HTTP bridge ledger with the current release head.

The operation-ledger revisions were authored on the recovery branch while
main continued to receive additive schema revisions.  This no-op merge keeps
Alembic at one head without rewriting either already-applied lineage.
"""

from __future__ import annotations

revision = "20260807_000000_merge_http_bridge_operation_spool_and_latest_main"
down_revision = (
    "20260806_120000_add_http_bridge_owner_process_epoch",
    "20260805_000001_finalize_http_bridge_operation_spool",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
