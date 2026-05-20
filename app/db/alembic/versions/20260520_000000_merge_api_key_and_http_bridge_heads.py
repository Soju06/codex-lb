"""merge API key model-scope and HTTP bridge heads

Revision ID: 20260520_000000_merge_api_key_and_http_bridge_heads
Revises: 20260513_000000_add_api_key_apply_to_codex_model,
    20260518_010000_merge_http_bridge_and_sqlite_recovery_heads
Create Date: 2026-05-20
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260520_000000_merge_api_key_and_http_bridge_heads"
down_revision = (
    "20260513_000000_add_api_key_apply_to_codex_model",
    "20260518_010000_merge_http_bridge_and_sqlite_recovery_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
