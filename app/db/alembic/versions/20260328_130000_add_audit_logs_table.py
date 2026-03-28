from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_130000_add_audit_logs_table"
down_revision = "20260328_120000_add_request_log_ttft"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("action", sa.String(length=100), nullable=False),
            sa.Column("actor_ip", sa.String(length=50), nullable=True),
            sa.Column("details", sa.Text(), nullable=True),
            sa.Column("request_id", sa.String(length=100), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    index_names = {index["name"] for index in inspector.get_indexes("audit_logs")}
    if "ix_audit_logs_action" not in index_names:
        op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    if "ix_audit_logs_timestamp" not in index_names:
        op.create_index("ix_audit_logs_timestamp", "audit_logs", ["timestamp"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("audit_logs"):
        return

    index_names = {index["name"] for index in inspector.get_indexes("audit_logs")}
    if "ix_audit_logs_timestamp" in index_names:
        op.drop_index("ix_audit_logs_timestamp", table_name="audit_logs")
    if "ix_audit_logs_action" in index_names:
        op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")
