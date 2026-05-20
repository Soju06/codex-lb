"""merge codex model flag and runtime heads

Revision ID: 20260519_000000_merge_codex_model_and_runtime_heads
Revises: 20260513_000000_add_api_key_apply_to_codex_model,
    20260518_010000_merge_http_bridge_and_sqlite_recovery_heads
Create Date: 2026-05-19
"""

from __future__ import annotations

revision = "20260519_000000_merge_codex_model_and_runtime_heads"
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
