"""add routing policy persistence

Revision ID: 20260601_010000_add_routing_policy_persistence
Revises: 20260509_000000_add_api_key_traffic_class
Create Date: 2026-06-01 18:30:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260601_010000_add_routing_policy_persistence"
down_revision = "20260509_000000_add_api_key_traffic_class"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _add_column_if_missing(
    connection: Connection,
    table_name: str,
    column_name: str,
    column: sa.Column,
) -> None:
    columns = _columns(connection, table_name)
    if not columns or column_name in columns:
        return
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.add_column(column)


def upgrade() -> None:
    bind = op.get_bind()
    _add_column_if_missing(
        bind,
        "accounts",
        "routing_policy",
        sa.Column("routing_policy", sa.String(), nullable=False, server_default=sa.text("'normal'")),
    )
    _add_column_if_missing(
        bind,
        "dashboard_settings",
        "additional_quota_routing_policies_json",
        sa.Column(
            "additional_quota_routing_policies_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    # The columns are owned by earlier active revisions in the merged graph:
    # 20260506_000000_add_account_routing_policy and
    # 20260509_010000_add_additional_quota_routing_policies. This revision only
    # keeps upgrade idempotent for branches that replayed the fields, so its
    # downgrade must not remove schema required by those earlier revisions.
    return
