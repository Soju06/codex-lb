"""add cache locality settings columns to dashboard_settings

Revision ID: 20260329_000000_add_dashboard_settings_cache_locality_columns
Revises: 20260325_000000_add_request_log_cost
Create Date: 2026-03-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260329_000000_add_dashboard_settings_cache_locality_columns"
down_revision = "20260325_000000_add_request_log_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add http_responses_session_bridge_prompt_cache_idle_ttl_seconds column
    op.add_column(
        "dashboard_settings",
        sa.Column(
            "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
            sa.Integer(),
            server_default=sa.text("3600"),
            nullable=False,
        ),
    )
    # Add sticky_reallocation_budget_threshold_pct column
    op.add_column(
        "dashboard_settings",
        sa.Column(
            "sticky_reallocation_budget_threshold_pct",
            sa.Float(),
            server_default=sa.text("95.0"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("dashboard_settings", "sticky_reallocation_budget_threshold_pct")
    op.drop_column("dashboard_settings", "http_responses_session_bridge_prompt_cache_idle_ttl_seconds")
