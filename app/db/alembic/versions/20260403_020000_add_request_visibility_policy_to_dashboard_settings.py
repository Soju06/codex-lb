from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260403_020000_add_request_visibility_policy_to_dashboard_settings"
down_revision = "20260402_210000_add_request_log_visibility_blob"
branch_labels = None
depends_on = None


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "request_visibility_mode" not in columns:
            batch_op.add_column(
                sa.Column(
                    "request_visibility_mode",
                    sa.String(),
                    nullable=False,
                    server_default=sa.text("'off'"),
                )
            )
        if "request_visibility_expires_at" not in columns:
            batch_op.add_column(sa.Column("request_visibility_expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "dashboard_settings")
    if not columns:
        return

    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "request_visibility_expires_at" in columns:
            batch_op.drop_column("request_visibility_expires_at")
        if "request_visibility_mode" in columns:
            batch_op.drop_column("request_visibility_mode")
