from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_000000_add_api_key_traffic_class"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "traffic_class" in _columns("api_keys"):
        return

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.add_column(
            sa.Column(
                "traffic_class",
                sa.String(),
                server_default="foreground",
                nullable=False,
            )
        )


def downgrade() -> None:
    if "traffic_class" not in _columns("api_keys"):
        return

    with op.batch_alter_table("api_keys") as batch_op:
        batch_op.drop_column("traffic_class")
