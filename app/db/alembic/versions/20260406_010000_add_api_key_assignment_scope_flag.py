"""add api key assignment scope flag

Revision ID: 20260406_010000_add_api_key_assignment_scope_flag
Revises: 20260406_000000_add_api_key_account_assignments
Create Date: 2026-04-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260406_010000_add_api_key_assignment_scope_flag"
down_revision = "20260406_000000_add_api_key_account_assignments"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "account_assignment_scope_enabled" not in existing_columns:
            batch_op.add_column(
                sa.Column(
                    "account_assignment_scope_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )

    if _table_exists(bind, "api_key_accounts"):
        bind.execute(
            sa.text(
                """
                UPDATE api_keys
                SET account_assignment_scope_enabled = :enabled
                WHERE EXISTS (
                    SELECT 1
                    FROM api_key_accounts
                    WHERE api_key_accounts.api_key_id = api_keys.id
                )
                """
            ),
            {"enabled": True},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "account_assignment_scope_enabled" in existing_columns:
            batch_op.drop_column("account_assignment_scope_enabled")
