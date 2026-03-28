from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_140000_add_scheduler_leader_table"
down_revision = "20260328_130000_add_audit_logs_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("scheduler_leader"):
        op.create_table(
            "scheduler_leader",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("leader_id", sa.String(length=100), nullable=False),
            sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    index_names = {index["name"] for index in inspector.get_indexes("scheduler_leader")}
    if "ix_scheduler_leader_expires_at" not in index_names:
        op.create_index("ix_scheduler_leader_expires_at", "scheduler_leader", ["expires_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("scheduler_leader"):
        return

    index_names = {index["name"] for index in inspector.get_indexes("scheduler_leader")}
    if "ix_scheduler_leader_expires_at" in index_names:
        op.drop_index("ix_scheduler_leader_expires_at", table_name="scheduler_leader")
    op.drop_table("scheduler_leader")
