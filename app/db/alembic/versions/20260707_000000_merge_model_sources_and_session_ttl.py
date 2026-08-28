"""merge model sources chain with dashboard session ttl hardening

Revision ID: 20260707_000000_merge_model_sources_and_session_ttl
Revises: 20260706_000000_add_model_source_audio_per_minute, 20260705_000000_harden_dashboard_session_ttl
Create Date: 2026-07-07 00:00:00.000000
"""

from __future__ import annotations

revision = "20260707_000000_merge_model_sources_and_session_ttl"
down_revision = (
    "20260706_000000_add_model_source_audio_per_minute",
    "20260705_000000_harden_dashboard_session_ttl",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
