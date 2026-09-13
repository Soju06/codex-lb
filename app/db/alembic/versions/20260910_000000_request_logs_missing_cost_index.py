"""Index eligible missing costs so background repair never scans priced history."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260910_000000_request_logs_missing_cost_index"
down_revision = "20260909_130000_add_request_logs_live_facet_indexes"
branch_labels = None
depends_on = None

_NAME = "idx_logs_missing_cost"
_PREDICATE = (
    "cost_usd IS NULL AND model_source_id IS NULL AND input_tokens IS NOT NULL "
    "AND (output_tokens IS NOT NULL OR reasoning_tokens IS NOT NULL) "
    "AND (model_source_kind IS NULL OR model_source_kind = 'subscription')"
)


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
            op.execute(
                sa.text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {_NAME} "
                    f"ON request_logs (model_source_id, id) WHERE {_PREDICATE}"
                )
            )
        return
    op.execute(sa.text(f"CREATE INDEX IF NOT EXISTS {_NAME} ON request_logs (model_source_id, id) WHERE {_PREDICATE}"))


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute(sa.text(f"DROP INDEX CONCURRENTLY IF EXISTS {_NAME}"))
        return
    op.drop_index(_NAME, table_name="request_logs", if_exists=True)
