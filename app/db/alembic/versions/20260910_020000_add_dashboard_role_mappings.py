"""Add dashboard_role_mappings.

Revision ID: 20260910_020000_add_dashboard_role_mappings
Revises: 20260910_010000_add_dashboard_auth_providers
Create Date: 2026-09-10

One row per "identities like this get that role" rule of a sign-in provider.
The table is created empty: an upgrade introduces no rule, and a provider
without rules re-evaluates nobody (D10), so the behaviour of an existing
install is unchanged until an operator adds the first rule.

``UNIQUE(provider, provider_key, priority)`` is declared inline because SQLite
cannot add a constraint to an existing table; ``role_id`` is RESTRICT so a role
a rule hands out cannot be deleted while the rule exists.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_020000_add_dashboard_role_mappings"
down_revision = "20260910_010000_add_dashboard_auth_providers"
branch_labels = None
depends_on = None

_TABLE = "dashboard_role_mappings"
_PROVIDER_INDEX = "idx_dashboard_role_mappings_provider"
_PRIORITY_CONSTRAINT = "uq_dashboard_role_mappings_priority"


def _columns() -> tuple[sa.Column, ...]:
    """Fresh Column objects per call: a Column may be bound to one table only."""

    return (
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("claim_name", sa.String(length=32), nullable=False),
        sa.Column("claim_value", sa.String(length=320), nullable=False),
        sa.Column("role_id", sa.String(length=36), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _index_names(bind: sa.engine.Connection) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {name for index in inspector.get_indexes(_TABLE) if isinstance(name := index.get("name"), str)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            *_columns(),
            sa.ForeignKeyConstraint(["role_id"], ["dashboard_roles.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("provider", "provider_key", "priority", name=_PRIORITY_CONSTRAINT),
        )
    if _PROVIDER_INDEX not in _index_names(bind):
        op.create_index(_PROVIDER_INDEX, _TABLE, ["provider", "provider_key"])


def downgrade() -> None:
    bind = op.get_bind()
    if _PROVIDER_INDEX in _index_names(bind):
        op.drop_index(_PROVIDER_INDEX, table_name=_TABLE)
    if sa.inspect(bind).has_table(_TABLE):
        op.drop_table(_TABLE)
