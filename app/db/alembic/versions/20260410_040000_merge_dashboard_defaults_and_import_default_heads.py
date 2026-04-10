"""merge dashboard defaults and import default heads

Revision ID: 20260410_040000_merge_dashboard_defaults_and_import_default_heads
Revises: 20260410_000000_backfill_pristine_dashboard_settings_defaults,
20260410_030000_restore_import_without_overwrite_default_true
Create Date: 2026-04-10
"""

from __future__ import annotations

revision = "20260410_040000_merge_dashboard_defaults_and_import_default_heads"
down_revision = (
    "20260410_000000_backfill_pristine_dashboard_settings_defaults",
    "20260410_030000_restore_import_without_overwrite_default_true",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    return


def downgrade() -> None:
    return
