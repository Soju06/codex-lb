from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_120000_add_request_log_ttft"
down_revision = "20260328_000000_add_rate_limit_attempts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "latency_first_token_ms" in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.add_column(sa.Column("latency_first_token_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("request_logs"):
        return

    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    if "latency_first_token_ms" not in columns:
        return

    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.drop_column("latency_first_token_ms")
