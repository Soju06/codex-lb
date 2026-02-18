"""add api_key_usage_reservations and reservation_items tables

Revision ID: 011_add_api_key_usage_reservations
Revises: 010_add_idx_logs_requested_at
Create Date: 2026-02-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "011_add_api_key_usage_reservations"
down_revision = "010_add_idx_logs_requested_at"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _index_exists(connection: Connection, index_name: str, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return False
    indexes = inspector.get_indexes(table_name)
    return any(idx["name"] == index_name for idx in indexes)


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "api_key_usage_reservations"):
        op.create_table(
            "api_key_usage_reservations",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column(
                "api_key_id",
                sa.String(),
                sa.ForeignKey("api_keys.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("input_tokens", sa.BigInteger(), nullable=True),
            sa.Column("output_tokens", sa.BigInteger(), nullable=True),
            sa.Column("cached_input_tokens", sa.BigInteger(), nullable=True),
            sa.Column("cost_microdollars", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        )
        if not _index_exists(bind, "idx_api_key_usage_reservations_key_id", "api_key_usage_reservations"):
            op.create_index(
                "idx_api_key_usage_reservations_key_id",
                "api_key_usage_reservations",
                ["api_key_id"],
            )
        if not _index_exists(bind, "idx_api_key_usage_reservations_status", "api_key_usage_reservations"):
            op.create_index(
                "idx_api_key_usage_reservations_status",
                "api_key_usage_reservations",
                ["status"],
            )

    if not _table_exists(bind, "api_key_usage_reservation_items"):
        op.create_table(
            "api_key_usage_reservation_items",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "reservation_id",
                sa.String(),
                sa.ForeignKey("api_key_usage_reservations.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "limit_id",
                sa.Integer(),
                sa.ForeignKey("api_key_limits.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("limit_type", sa.String(), nullable=False),
            sa.Column("reserved_delta", sa.BigInteger(), nullable=False),
            sa.Column("actual_delta", sa.BigInteger(), nullable=True),
            sa.Column("expected_reset_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("reservation_id", "limit_id", name="uq_reservation_limit"),
        )
        if not _index_exists(bind, "idx_api_key_usage_res_items_reservation_id", "api_key_usage_reservation_items"):
            op.create_index(
                "idx_api_key_usage_res_items_reservation_id",
                "api_key_usage_reservation_items",
                ["reservation_id"],
            )


def downgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "api_key_usage_reservation_items"):
        if _index_exists(bind, "idx_api_key_usage_res_items_reservation_id", "api_key_usage_reservation_items"):
            op.drop_index(
                "idx_api_key_usage_res_items_reservation_id",
                table_name="api_key_usage_reservation_items",
            )
        op.drop_table("api_key_usage_reservation_items")

    if _table_exists(bind, "api_key_usage_reservations"):
        if _index_exists(bind, "idx_api_key_usage_reservations_status", "api_key_usage_reservations"):
            op.drop_index("idx_api_key_usage_reservations_status", table_name="api_key_usage_reservations")
        if _index_exists(bind, "idx_api_key_usage_reservations_key_id", "api_key_usage_reservations"):
            op.drop_index("idx_api_key_usage_reservations_key_id", table_name="api_key_usage_reservations")
        op.drop_table("api_key_usage_reservations")
