"""merge API key assignment and capability lineage heads

Revision ID: 20260802_000000_merge_assignment_and_capability_heads
Revises:
- 20260727_000000_add_api_key_assignment_generation
- 20260731_000000_add_capability_lineage_markers
Create Date: 2026-08-02 00:00:00.000000
"""

from __future__ import annotations

revision = "20260802_000000_merge_assignment_and_capability_heads"
down_revision = (
    "20260727_000000_add_api_key_assignment_generation",
    "20260731_000000_add_capability_lineage_markers",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
