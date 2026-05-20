"""merge runtime and API key bridge heads

Revision ID: 20260520_010000_merge_runtime_and_api_key_bridge_heads
Revises: 20260519_000000_merge_codex_model_and_runtime_heads,
    20260520_000000_merge_api_key_and_http_bridge_heads
Create Date: 2026-05-20
"""

from __future__ import annotations

revision = "20260520_010000_merge_runtime_and_api_key_bridge_heads"
down_revision = (
    "20260519_000000_merge_codex_model_and_runtime_heads",
    "20260520_000000_merge_api_key_and_http_bridge_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
