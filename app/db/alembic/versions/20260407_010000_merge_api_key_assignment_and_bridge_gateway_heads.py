"""merge api key assignment and bridge gateway heads

Revision ID: 20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads
Revises: 20260406_010000_add_api_key_assignment_scope_flag, 20260407_000000_add_bridge_gateway_safe_mode
Create Date: 2026-04-07
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads"
down_revision = (
    "20260406_010000_add_api_key_assignment_scope_flag",
    "20260407_000000_add_bridge_gateway_safe_mode",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return None


def downgrade() -> None:
    return None
