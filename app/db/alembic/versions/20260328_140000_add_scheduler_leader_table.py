from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_140000_add_scheduler_leader_table"
down_revision = "20260328_130000_add_audit_logs_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # For SQLite, use a short busy timeout to avoid long locks during DDL.
    if bind.dialect.name == "sqlite":
        bind.execute(sa.text("PRAGMA busy_timeout=1000"))

    # Create table with IF NOT EXISTS to avoid schema inspection and reduce lock scope.
    op.execute("CREATE TABLE IF NOT EXISTS scheduler_leader ("
               "id INTEGER NOT NULL PRIMARY KEY, "
               "leader_id VARCHAR(100) NOT NULL, "
               "acquired_at DATETIME NOT NULL, "
               "expires_at DATETIME NOT NULL)")

    # Create index with IF NOT EXISTS (supported in SQLite 3.8+).
    op.execute("CREATE INDEX IF NOT EXISTS ix_scheduler_leader_expires_at "
               "ON scheduler_leader (expires_at)")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("scheduler_leader"):
        return

    index_names = {index["name"] for index in inspector.get_indexes("scheduler_leader")}
    if "ix_scheduler_leader_expires_at" in index_names:
        op.drop_index("ix_scheduler_leader_expires_at", table_name="scheduler_leader")
    op.drop_table("scheduler_leader")
