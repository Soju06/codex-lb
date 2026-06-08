"""Rename legacy free-account usage windows before monthly rollout.

Revision ID: 20260603_000000_free_account_monthly_window
Revises: 20260604_000000_add_reauth_required_account_status
Create Date: 2026-06-03 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260603_000000_free_account_monthly_window"
down_revision = "20260604_000000_add_reauth_required_account_status"
branch_labels = None
depends_on = None

usage_history = sa.table(
    "usage_history",
    sa.column("window", sa.String),
    sa.column("account_id", sa.String),
)

accounts = sa.table(
    "accounts",
    sa.column("id", sa.String),
    sa.column("plan_type", sa.String),
)


def upgrade() -> None:
    free_ids = sa.select(accounts.c.id).where(accounts.c.plan_type == "free")
    op.execute(
        sa.update(usage_history)
        .where(sa.or_(usage_history.c.window == "primary", usage_history.c.window.is_(None)))
        .where(usage_history.c.account_id.in_(free_ids))
        .values(window="old-primary")
    )
    op.execute(
        sa.update(usage_history)
        .where(usage_history.c.window == "secondary")
        .where(usage_history.c.account_id.in_(free_ids))
        .values(window="old-secondary")
    )


def downgrade() -> None:
    free_ids = sa.select(accounts.c.id).where(accounts.c.plan_type == "free")
    op.execute(
        sa.update(usage_history)
        .where(usage_history.c.window == "old-primary")
        .where(usage_history.c.account_id.in_(free_ids))
        .values(window="primary")
    )
    op.execute(
        sa.update(usage_history)
        .where(usage_history.c.window == "old-secondary")
        .where(usage_history.c.account_id.in_(free_ids))
        .values(window="secondary")
    )
