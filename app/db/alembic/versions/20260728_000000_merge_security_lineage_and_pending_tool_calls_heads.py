"""Merge security-lineage and pending tool call manifest heads.

Revision ID: 20260728_000000_merge_security_lineage_and_pending_tool_calls_heads
Revises: 20260722_000000_add_security_lineage_persistence, 20260725_000000_add_http_bridge_pending_tool_calls
Create Date: 2026-07-28
"""

revision = "20260728_000000_merge_security_lineage_and_pending_tool_calls_heads"
down_revision = (
    "20260722_000000_add_security_lineage_persistence",
    "20260725_000000_add_http_bridge_pending_tool_calls",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
