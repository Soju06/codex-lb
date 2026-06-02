"""add upstream proxy routing

Revision ID: 20260602_050000_add_upstream_proxy_routing
Revises: 20260602_060000_merge_account_workspace_and_failure_heads
Create Date: 2026-06-01 15:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260602_050000_add_upstream_proxy_routing"
down_revision = "20260602_060000_merge_account_workspace_and_failure_heads"
branch_labels = None
depends_on = None


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {column["name"] for column in sa.inspect(connection).get_columns(table_name)}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {name for index in sa.inspect(connection).get_indexes(table_name) if (name := index["name"]) is not None}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "proxy_endpoints"):
        op.create_table(
            "proxy_endpoints",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("scheme", sa.String(), nullable=False),
            sa.Column("host", sa.String(), nullable=False),
            sa.Column("port", sa.Integer(), nullable=False),
            sa.Column("username", sa.String(), nullable=True),
            sa.Column("password_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_table(bind, "proxy_pools"):
        op.create_table(
            "proxy_pools",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_table(bind, "proxy_pool_members"):
        op.create_table(
            "proxy_pool_members",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("pool_id", sa.String(), nullable=False),
            sa.Column("endpoint_id", sa.String(), nullable=False),
            sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("weight", sa.Integer(), server_default=sa.text("1"), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["endpoint_id"], ["proxy_endpoints.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["pool_id"], ["proxy_pools.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("pool_id", "endpoint_id", name="uq_proxy_pool_members_pool_endpoint"),
        )
    if _has_table(bind, "proxy_pool_members") and "idx_proxy_pool_members_pool_order" not in _indexes(
        bind, "proxy_pool_members"
    ):
        op.create_index(
            "idx_proxy_pool_members_pool_order",
            "proxy_pool_members",
            ["pool_id", "is_active", "sort_order", "id"],
        )
    if not _has_table(bind, "account_proxy_bindings"):
        op.create_table(
            "account_proxy_bindings",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("pool_id", sa.String(), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["pool_id"], ["proxy_pools.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("account_id", name="uq_account_proxy_bindings_account"),
        )
    dashboard_columns = _columns(bind, "dashboard_settings")
    if dashboard_columns and (
        "upstream_proxy_routing_enabled" not in dashboard_columns
        or "upstream_proxy_default_pool_id" not in dashboard_columns
    ):
        with op.batch_alter_table("dashboard_settings") as batch_op:
            if "upstream_proxy_routing_enabled" not in dashboard_columns:
                batch_op.add_column(
                    sa.Column("upstream_proxy_routing_enabled", sa.Boolean(), server_default=sa.false(), nullable=False)
                )
            if "upstream_proxy_default_pool_id" not in dashboard_columns:
                batch_op.add_column(sa.Column("upstream_proxy_default_pool_id", sa.String(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_dashboard_settings_upstream_proxy_default_pool",
                    "proxy_pools",
                    ["upstream_proxy_default_pool_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
    request_log_columns = _columns(bind, "request_logs")
    if request_log_columns:
        if "upstream_proxy_route_mode" not in request_log_columns:
            op.add_column("request_logs", sa.Column("upstream_proxy_route_mode", sa.String(), nullable=True))
        if "upstream_proxy_pool_id" not in request_log_columns:
            op.add_column("request_logs", sa.Column("upstream_proxy_pool_id", sa.String(), nullable=True))
        if "upstream_proxy_endpoint_id" not in request_log_columns:
            op.add_column("request_logs", sa.Column("upstream_proxy_endpoint_id", sa.String(), nullable=True))
        if "upstream_proxy_fallback_used" not in request_log_columns:
            op.add_column("request_logs", sa.Column("upstream_proxy_fallback_used", sa.Boolean(), nullable=True))
        if "upstream_proxy_fail_closed_reason" not in request_log_columns:
            op.add_column("request_logs", sa.Column("upstream_proxy_fail_closed_reason", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    request_log_columns = _columns(bind, "request_logs")
    for column_name in (
        "upstream_proxy_fail_closed_reason",
        "upstream_proxy_fallback_used",
        "upstream_proxy_endpoint_id",
        "upstream_proxy_pool_id",
        "upstream_proxy_route_mode",
    ):
        if column_name in request_log_columns:
            op.drop_column("request_logs", column_name)
    dashboard_columns = _columns(bind, "dashboard_settings")
    if "upstream_proxy_default_pool_id" in dashboard_columns or "upstream_proxy_routing_enabled" in dashboard_columns:
        with op.batch_alter_table("dashboard_settings") as batch_op:
            if "upstream_proxy_default_pool_id" in dashboard_columns:
                batch_op.drop_constraint("fk_dashboard_settings_upstream_proxy_default_pool", type_="foreignkey")
                batch_op.drop_column("upstream_proxy_default_pool_id")
            if "upstream_proxy_routing_enabled" in dashboard_columns:
                batch_op.drop_column("upstream_proxy_routing_enabled")
    if _has_table(bind, "account_proxy_bindings"):
        op.drop_table("account_proxy_bindings")
    if _has_table(bind, "proxy_pool_members") and "idx_proxy_pool_members_pool_order" in _indexes(
        bind, "proxy_pool_members"
    ):
        op.drop_index("idx_proxy_pool_members_pool_order", table_name="proxy_pool_members")
    if _has_table(bind, "proxy_pool_members"):
        op.drop_table("proxy_pool_members")
    if _has_table(bind, "proxy_pools"):
        op.drop_table("proxy_pools")
    if _has_table(bind, "proxy_endpoints"):
        op.drop_table("proxy_endpoints")
