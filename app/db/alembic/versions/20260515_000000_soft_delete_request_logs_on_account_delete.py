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


def _request_log_account_fk_names(inspector: sa.Inspector) -> list[str]:
    names: list[str] = []
    for foreign_key in inspector.get_foreign_keys("request_logs"):
        if foreign_key.get("referred_table") != "accounts":
            continue
        if foreign_key.get("constrained_columns") != ["account_id"]:
            continue
        if foreign_key.get("referred_columns") != ["id"]:
            continue
        name = foreign_key.get("name")
        if name:
            names.append(str(name))
            continue
        names.append(_REQUEST_LOG_ACCOUNT_FK)
    return names


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {column["name"] for column in inspector.get_columns("request_logs")}
    existing_fk_names = _request_log_account_fk_names(inspector)

    with op.batch_alter_table("request_logs", naming_convention=_FK_NAMING) as batch_op:
        if "deleted_at" not in existing_columns:
            batch_op.add_column(sa.Column("deleted_at", sa.DateTime(), nullable=True))
        for fk_name in existing_fk_names:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_fk_names = _request_log_account_fk_names(inspector)

    op.drop_index("idx_logs_deleted_at_requested_at_id", table_name="request_logs", if_exists=True)
    with op.batch_alter_table("request_logs", naming_convention=_FK_NAMING) as batch_op:
        for fk_name in existing_fk_names:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        batch_op.create_foreign_key(
            _REQUEST_LOG_ACCOUNT_FK,
            "accounts",
            ["account_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("deleted_at")
