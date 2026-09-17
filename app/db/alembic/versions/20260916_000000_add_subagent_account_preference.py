"""Add the dashboard subagent account preference.

Revision ID: 20260916_000000_add_subagent_account_preference
Revises: 20260913_000000_add_oidc_provider_flow
Create Date: 2026-09-16

The non-null ``off`` default preserves routing behavior for existing rows and
new installs until an operator explicitly enables the preference.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260916_000000_add_subagent_account_preference"
down_revision = "20260913_000000_add_oidc_provider_flow"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_COLUMN = "subagent_account_preference"


def _has_column() -> bool:
    return _COLUMN in {column["name"] for column in sa.inspect(op.get_bind()).get_columns(_TABLE)}


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(), nullable=False, server_default=sa.text("'off'")),
        )


def downgrade() -> None:
    if _has_column():
        op.drop_column(_TABLE, _COLUMN)
