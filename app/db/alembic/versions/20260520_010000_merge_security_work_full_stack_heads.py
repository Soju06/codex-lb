"""merge security-work routing with current stack heads

Revision ID: 20260520_010000_merge_security_work_full_stack_heads
Revises: 20260520_000000_merge_routing_policy_full_stack_heads,
    20260519_000000_merge_security_work_http_bridge_sqlite_heads
Create Date: 2026-05-20
"""

from __future__ import annotations

revision = "20260520_010000_merge_security_work_full_stack_heads"
down_revision = (
    "20260520_000000_merge_routing_policy_full_stack_heads",
    "20260519_000000_merge_security_work_http_bridge_sqlite_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
