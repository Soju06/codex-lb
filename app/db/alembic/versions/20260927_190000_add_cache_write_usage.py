"""Preserve cache-write usage in request logs and finalized reservations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_190000_add_cache_write_usage"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    for table, column_type in (("request_logs", sa.Integer()), ("api_key_usage_reservations", sa.BigInteger())):
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch_op:
            if "cache_write_input_tokens" not in columns:
                batch_op.add_column(sa.Column("cache_write_input_tokens", column_type, nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table in ("api_key_usage_reservations", "request_logs"):
        columns = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        with op.batch_alter_table(table) as batch_op:
            if "cache_write_input_tokens" in columns:
                batch_op.drop_column("cache_write_input_tokens")
