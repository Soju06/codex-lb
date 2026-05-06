from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260506_000000_add_account_routing_policy"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "routing_policy" in _columns("accounts"):
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "routing_policy",
                sa.String(),
                server_default="normal",
                nullable=False,
            )
        )


def downgrade() -> None:
    if "routing_policy" not in _columns("accounts"):
        return

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_column("routing_policy")
