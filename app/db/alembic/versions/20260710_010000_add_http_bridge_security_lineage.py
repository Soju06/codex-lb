"""Persist security-work authorization for HTTP bridge lineages.

Revision ID: 20260710_010000_add_http_bridge_security_lineage
Revises: 20260716_000000_add_oauth_device_flow_slots
"""

import sqlalchemy as sa
from alembic import op

revision = "20260710_010000_add_http_bridge_security_lineage"
down_revision = "20260716_000000_add_oauth_device_flow_slots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("http_bridge_sessions")}
    if "requires_security_work_authorized" not in columns:
        op.add_column(
            "http_bridge_sessions",
            sa.Column(
                "requires_security_work_authorized",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("http_bridge_sessions")}
    if "requires_security_work_authorized" in columns:
        op.drop_column("http_bridge_sessions", "requires_security_work_authorized")
