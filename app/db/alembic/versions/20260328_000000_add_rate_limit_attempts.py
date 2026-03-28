from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260328_000000_add_rate_limit_attempts"
down_revision = "20260325_000000_add_request_log_cost"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("rate_limit_attempts"):
        op.create_table(
            "rate_limit_attempts",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("key", sa.String(length=255), nullable=False),
            sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("type", sa.String(length=50), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    index_names = {index["name"] for index in inspector.get_indexes("rate_limit_attempts")}
    if "ix_rate_limit_attempts_key" not in index_names:
        op.create_index("ix_rate_limit_attempts_key", "rate_limit_attempts", ["key"], unique=False)
    if "ix_rate_limit_attempts_type_key_attempted_at" not in index_names:
        op.create_index(
            "ix_rate_limit_attempts_type_key_attempted_at",
            "rate_limit_attempts",
            ["type", "key", "attempted_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("rate_limit_attempts"):
        return

    index_names = {index["name"] for index in inspector.get_indexes("rate_limit_attempts")}
    if "ix_rate_limit_attempts_type_key_attempted_at" in index_names:
        op.drop_index("ix_rate_limit_attempts_type_key_attempted_at", table_name="rate_limit_attempts")
    if "ix_rate_limit_attempts_key" in index_names:
        op.drop_index("ix_rate_limit_attempts_key", table_name="rate_limit_attempts")
    op.drop_table("rate_limit_attempts")
