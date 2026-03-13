"""merge dashboard settings and enum normalization heads

Revision ID: 20260313_190000_merge_dashboard_and_enum_heads
Revises:
    20260309_000000_add_dashboard_settings_http_proxy_url,
    20260313_170000_normalize_legacy_enum_value_casing
Create Date: 2026-03-13 19:00:00.000000
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260313_190000_merge_dashboard_and_enum_heads"
down_revision = (
    "20260309_000000_add_dashboard_settings_http_proxy_url",
    "20260313_170000_normalize_legacy_enum_value_casing",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
