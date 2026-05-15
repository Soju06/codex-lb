"""soft delete request_logs on account delete

Revision ID: 20260515_000000_soft_delete_request_logs_on_account_delete
Revises: 20260514_000000_add_request_logs_api_key_time_index
Create Date: 2026-05-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260515_000000_soft_delete_request_logs_on_account_delete"
down_revision = "20260514_000000_add_request_logs_api_key_time_index"
branch_labels = None
depends_on = None

_FK_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
_REQUEST_LOG_ACCOUNT_FK = "fk_request_logs_account_id_accounts"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("request_logs")}

    with op.batch_alter_table("request_logs", naming_convention=_FK_NAMING) as batch_op:
        if "deleted_at" not in existing_columns:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        batch_op.drop_constraint(_REQUEST_LOG_ACCOUNT_FK, type_="foreignkey")
        batch_op.create_foreign_key(
            _REQUEST_LOG_ACCOUNT_FK,
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "idx_logs_deleted_at_requested_at_id",
        "request_logs",
        ["deleted_at", sa.text("requested_at DESC"), sa.text("id DESC")],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_logs_deleted_at_requested_at_id", table_name="request_logs", if_exists=True)
    with op.batch_alter_table("request_logs", naming_convention=_FK_NAMING) as batch_op:
        batch_op.drop_constraint(_REQUEST_LOG_ACCOUNT_FK, type_="foreignkey")
        batch_op.create_foreign_key(
            _REQUEST_LOG_ACCOUNT_FK,
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("deleted_at")
