"""Index the live thread-pin count so a dashboard poll never walks model_source_pins."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260911_000000_model_source_pins_kind_expires_index"
down_revision = "20260910_020000_add_dashboard_role_mappings"
branch_labels = None
depends_on = None

_NAME = "ix_model_source_pins_kind_expires_at"
_COLUMNS = "(kind, expires_at)"


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            invalid = (
                op.get_bind()
                .execute(
                    sa.text(
                        "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                        "WHERE c.relname = :name AND NOT i.indisvalid"
                    ),
                    {"name": _NAME},
                )
                .scalar()
            )
            if invalid:
                op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_NAME}"))
            op.execute(sa.text(f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_NAME} ON model_source_pins {_COLUMNS}"))
        return
    op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {_NAME} ON model_source_pins {_COLUMNS}"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_NAME}"))
        return
    op.drop_index(_NAME, table_name="model_source_pins", if_exists=True)
