"""normalize account plan types

Revision ID: 001_normalize_account_plan_types
Revises: 000_base_schema
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.auth import DEFAULT_PLAN
from app.core.plan_types import coerce_account_plan_type

# revision identifiers, used by Alembic.
revision = "001_normalize_account_plan_types"
down_revision = "000_base_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("accounts"):
        return

    rows = bind.execute(sa.text("SELECT id, plan_type FROM accounts")).fetchall()
    for row in rows:
        account_id = str(row[0])
        plan_type = str(row[1] or "")
        normalized = coerce_account_plan_type(plan_type, DEFAULT_PLAN)
        if normalized == plan_type:
            continue

        bind.execute(
            sa.text("UPDATE accounts SET plan_type = :plan_type WHERE id = :account_id"),
            {"plan_type": normalized, "account_id": account_id},
        )


def downgrade() -> None:
    # Irreversible data normalization.
    return
