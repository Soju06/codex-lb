"""add team_members table, api_keys.member_id and team-mode dashboard settings

Revision ID: 20260918_000000_add_team_members
Revises: 20260909_120000_add_reset_credit_attempts
Create Date: 2026-09-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260918_000000_add_team_members"
down_revision = "20260909_120000_add_reset_credit_attempts"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {index["name"] for index in inspector.get_indexes(table_name) if index.get("name")}


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "team_members"):
        op.create_table(
            "team_members",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False, unique=True),
            sa.Column("email", sa.String(), nullable=True),
            sa.Column(
                "status",
                sa.Enum("active", "suspended", name="team_member_status"),
                nullable=False,
                server_default=sa.text("'active'"),
            ),
            sa.Column("cost_cap_day_usd", sa.Numeric(12, 4), nullable=True),
            sa.Column("cost_cap_week_usd", sa.Numeric(12, 4), nullable=True),
            sa.Column("cost_cap_month_usd", sa.Numeric(12, 4), nullable=True),
            sa.Column("token_cap_day", sa.BigInteger(), nullable=True),
            sa.Column("token_cap_week", sa.BigInteger(), nullable=True),
            sa.Column("token_cap_month", sa.BigInteger(), nullable=True),
            sa.Column("allowed_models", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

    api_keys_columns = _columns(bind, "api_keys")
    if api_keys_columns and "member_id" not in api_keys_columns:
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.add_column(sa.Column("member_id", sa.String(), nullable=True))
            batch_op.create_foreign_key(
                "fk_api_keys_member_id",
                "team_members",
                ["member_id"],
                ["id"],
                ondelete="SET NULL",
            )

    if "idx_api_keys_member_id" not in _indexes(bind, "api_keys"):
        op.create_index("idx_api_keys_member_id", "api_keys", ["member_id"], unique=False)

    settings_columns = _columns(bind, "dashboard_settings")
    if settings_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            if "team_mode_enabled" not in settings_columns:
                batch_op.add_column(
                    sa.Column(
                        "team_mode_enabled",
                        sa.Boolean(),
                        nullable=False,
                        server_default=sa.false(),
                    )
                )
            if "team_public_base_url" not in settings_columns:
                batch_op.add_column(sa.Column("team_public_base_url", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    settings_columns = _columns(bind, "dashboard_settings")
    if settings_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            if "team_public_base_url" in settings_columns:
                batch_op.drop_column("team_public_base_url")
            if "team_mode_enabled" in settings_columns:
                batch_op.drop_column("team_mode_enabled")

    if "idx_api_keys_member_id" in _indexes(bind, "api_keys"):
        op.drop_index("idx_api_keys_member_id", table_name="api_keys")

    if "member_id" in _columns(bind, "api_keys"):
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.drop_column("member_id")

    if _table_exists(bind, "team_members"):
        op.drop_table("team_members")
        team_member_status = sa.Enum("active", "suspended", name="team_member_status")
        team_member_status.drop(bind, checkfirst=True)
