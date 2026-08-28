"""merge automations and model sources heads

Revision ID: 20260707_010000_merge_automations_and_model_sources_heads
Revises: 20260630_050000_add_automation_run_prompt_snapshot, 20260707_000000_merge_model_sources_and_session_ttl
Create Date: 2026-07-07 01:00:00.000000
"""

from __future__ import annotations

revision = "20260707_010000_merge_automations_and_model_sources_heads"
down_revision = (
    "20260630_050000_add_automation_run_prompt_snapshot",
    "20260707_000000_merge_model_sources_and_session_ttl",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
