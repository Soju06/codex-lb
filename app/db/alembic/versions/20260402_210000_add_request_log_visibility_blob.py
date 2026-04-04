from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260402_210000_add_request_log_visibility_blob"
down_revision = "20260402_000000_switch_dashboard_routing_default_to_capacity_weighted"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns or "request_visibility" in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("request_visibility", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "request_logs")
    if not columns or "request_visibility" not in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("request_visibility")
