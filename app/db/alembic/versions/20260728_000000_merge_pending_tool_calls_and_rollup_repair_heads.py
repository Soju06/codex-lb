"""merge pending-tool-call and stamped-rollup-repair heads

Revision ID: 20260728_000000_merge_pending_tool_calls_and_rollup_repair_heads
Revises:
- 20260725_000000_add_http_bridge_pending_tool_calls
- 20260726_000000_repair_request_usage_rollups_after_merge
Create Date: 2026-07-28

The pending-tool-call migration was added on a branch from the canonical
request-usage rollup revision, while the forward-only repair migration starts
from the deployed request-log merge. Keep both paths intact and converge them
before startup asks Alembic for ``head``.
"""

from __future__ import annotations

revision = "20260728_000000_merge_pending_tool_calls_and_rollup_repair_heads"
down_revision = (
    "20260725_000000_add_http_bridge_pending_tool_calls",
    "20260726_000000_repair_request_usage_rollups_after_merge",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
