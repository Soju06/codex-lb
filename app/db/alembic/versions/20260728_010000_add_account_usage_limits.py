"""add per-account usage limits

Revision ID: 20260728_010000_add_account_usage_limits
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260728_010000_add_account_usage_limits"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None

_TABLE = "accounts"
_ENABLED_COLUMN = "usage_limit_enabled"
_PERCENT_COLUMN = "usage_limit_percent"
_RANGE_CONSTRAINT = "ck_accounts_usage_limit_percent_range"
_ENABLED_REQUIRES_PERCENT_CONSTRAINT = "ck_accounts_usage_limit_enabled_requires_percent"


def _column_names(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def _check_constraint_names(connection: Connection) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(_TABLE):
        return set()
    return {
        str(constraint["name"])
        for constraint in inspector.get_check_constraints(_TABLE)
        if constraint.get("name") is not None
    }


def upgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    if not columns:
        return
    constraints = _check_constraint_names(bind)

    with op.batch_alter_table(_TABLE) as batch_op:
        if _ENABLED_COLUMN not in columns:
            batch_op.add_column(
                sa.Column(
                    _ENABLED_COLUMN,
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )
        if _PERCENT_COLUMN not in columns:
            batch_op.add_column(sa.Column(_PERCENT_COLUMN, sa.Float(), nullable=True))
        if _RANGE_CONSTRAINT not in constraints:
            batch_op.create_check_constraint(
                _RANGE_CONSTRAINT,
                "usage_limit_percent IS NULL OR (usage_limit_percent > 0 AND usage_limit_percent <= 100)",
            )
        if _ENABLED_REQUIRES_PERCENT_CONSTRAINT not in constraints:
            batch_op.create_check_constraint(
                _ENABLED_REQUIRES_PERCENT_CONSTRAINT,
                "NOT usage_limit_enabled OR usage_limit_percent IS NOT NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _column_names(bind)
    if not columns:
        return
    constraints = _check_constraint_names(bind)

    with op.batch_alter_table(_TABLE) as batch_op:
        if _ENABLED_REQUIRES_PERCENT_CONSTRAINT in constraints:
            batch_op.drop_constraint(_ENABLED_REQUIRES_PERCENT_CONSTRAINT, type_="check")
        if _RANGE_CONSTRAINT in constraints:
            batch_op.drop_constraint(_RANGE_CONSTRAINT, type_="check")
        if _PERCENT_COLUMN in columns:
            batch_op.drop_column(_PERCENT_COLUMN)
        if _ENABLED_COLUMN in columns:
            batch_op.drop_column(_ENABLED_COLUMN)
