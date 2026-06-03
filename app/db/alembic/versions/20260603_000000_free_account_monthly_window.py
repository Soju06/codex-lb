"""Rename legacy free-account usage windows before monthly rollout.

Revision ID: 20260603_000000_free_account_monthly_window
Revises: 20260602_050000_add_upstream_proxy_routing
Create Date: 2026-06-03 00:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260603_000000_free_account_monthly_window"
down_revision = "20260602_050000_add_upstream_proxy_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE usage_history
        SET window = 'old-primary'
        WHERE window = 'primary'
          AND account_id IN (SELECT id FROM accounts WHERE plan_type = 'free')
        """
    )
    op.execute(
        """
        UPDATE usage_history
        SET window = 'old-secondary'
        WHERE window = 'secondary'
          AND account_id IN (SELECT id FROM accounts WHERE plan_type = 'free')
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE usage_history
        SET window = 'primary'
        WHERE window = 'old-primary'
          AND account_id IN (SELECT id FROM accounts WHERE plan_type = 'free')
        """
    )
    op.execute(
        """
        UPDATE usage_history
        SET window = 'secondary'
        WHERE window = 'old-secondary'
          AND account_id IN (SELECT id FROM accounts WHERE plan_type = 'free')
        """
    )
