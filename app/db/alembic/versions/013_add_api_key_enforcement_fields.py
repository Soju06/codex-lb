"""add api key enforcement fields

Revision ID: 013_add_api_key_enforcement_fields
Revises: 012_add_import_without_overwrite_and_drop_accounts_email_unique
Create Date: 2026-03-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "013_add_api_key_enforcement_fields"
down_revision = "012_add_import_without_overwrite_and_drop_accounts_email_unique"
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
        if "enforced_model" not in existing_columns:
            batch_op.add_column(sa.Column("enforced_model", sa.String(), nullable=True))
        if "enforced_reasoning_effort" not in existing_columns:
            batch_op.add_column(sa.Column("enforced_reasoning_effort", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "api_keys"):
        return

    existing_columns = _columns(bind, "api_keys")
    with op.batch_alter_table("api_keys") as batch_op:
        if "enforced_reasoning_effort" in existing_columns:
            batch_op.drop_column("enforced_reasoning_effort")
        if "enforced_model" in existing_columns:
            batch_op.drop_column("enforced_model")
