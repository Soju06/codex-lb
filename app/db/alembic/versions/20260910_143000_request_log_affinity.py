"""Persist nullable resolved affinity metadata on request logs."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_143000_request_log_affinity"
down_revision = "20260910_010000_dashboard_spool_retention"
branch_labels = None
depends_on = None

_COLUMNS = ("sticky_key_source", "sticky_kind", "sticky_key_hash")


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("request_logs")}
    # Legacy bootstrap can start with the current ORM schema already present.
    with op.batch_alter_table("request_logs") as batch_op:
        for name in _COLUMNS:
            if name not in existing:
                batch_op.add_column(sa.Column(name, sa.String(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name in reversed(_COLUMNS):
            if name in existing:
                batch_op.drop_column(name)
