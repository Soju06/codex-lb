"""Record output timing evidence without reconstructing historical samples."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260915_010000_add_request_log_generation_evidence"
down_revision = "20260913_000000_add_oidc_provider_flow"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name in ("latency_first_output_ms", "output_delta_count", "latency_upstream_terminal_ms"):
            if name not in columns:
                batch_op.add_column(sa.Column(name, sa.Integer(), nullable=True))


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        for name in ("latency_upstream_terminal_ms", "output_delta_count", "latency_first_output_ms"):
            if name in columns:
                batch_op.drop_column(name)
