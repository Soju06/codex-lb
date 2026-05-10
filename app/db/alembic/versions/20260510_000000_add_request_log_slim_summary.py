from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_000000_add_request_log_slim_summary"
down_revision = "20260424_000000_merge_dashboard_session_ttl_and_request_log_heads"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if "slim_summary_json" in _columns("request_logs"):
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("slim_summary_json", sa.Text(), nullable=True))


def downgrade() -> None:
    if "slim_summary_json" not in _columns("request_logs"):
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("slim_summary_json")
