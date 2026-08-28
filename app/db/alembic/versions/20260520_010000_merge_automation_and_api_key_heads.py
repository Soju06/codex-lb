"""merge automation and API key model-scope heads

Revision ID: 20260520_010000_merge_automation_and_api_key_heads
Revises: 20260422_103000_normalize_legacy_automation_run_cycle_keys,
    20260520_010000_add_request_logs_api_key_account_index
Create Date: 2026-05-20
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260520_010000_merge_automation_and_api_key_heads"
down_revision = (
    "20260422_103000_normalize_legacy_automation_run_cycle_keys",
    "20260520_010000_add_request_logs_api_key_account_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
