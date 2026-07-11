"""merge daily aggregates and ttft observability heads

Revision ID: 20260711_000000_merge_daily_aggregates_and_ttft_heads
Revises: 20260702_000000_add_request_log_daily_aggregates, 20260709_000000_add_ttft_phase_observability
Create Date: 2026-07-11
"""

from __future__ import annotations

revision = "20260711_000000_merge_daily_aggregates_and_ttft_heads"
down_revision = (
    "20260702_000000_add_request_log_daily_aggregates",
    "20260709_000000_add_ttft_phase_observability",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
