"""add model_overrides and request actor fields

Revision ID: 014_add_model_overrides_and_request_actor_fields
Revises: 013_add_dashboard_settings_routing_strategy
Create Date: 2026-02-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "014_add_model_overrides_and_request_actor_fields"
down_revision = "013_add_dashboard_settings_routing_strategy"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _unique_constraints(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {
        str(constraint["name"])
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint.get("name") is not None
    }


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "model_overrides"):
        op.create_table(
            "model_overrides",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True, nullable=False),
            sa.Column("match_type", sa.String(), nullable=False),
            sa.Column("match_value", sa.String(), nullable=False),
            sa.Column("forced_model", sa.String(), nullable=False),
            sa.Column("forced_reasoning_effort", sa.String(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
            ),
        )

    constraints = _unique_constraints(bind, "model_overrides")
    if "uq_model_overrides_match" not in constraints:
        with op.batch_alter_table("model_overrides") as batch_op:
            batch_op.create_unique_constraint("uq_model_overrides_match", ["match_type", "match_value"])

    if _table_exists(bind, "request_logs"):
        columns = _columns(bind, "request_logs")
        with op.batch_alter_table("request_logs") as batch_op:
            if "requested_model" not in columns:
                batch_op.add_column(sa.Column("requested_model", sa.String(), nullable=True))
            if "client_ip" not in columns:
                batch_op.add_column(sa.Column("client_ip", sa.String(), nullable=True))
            if "client_app" not in columns:
                batch_op.add_column(sa.Column("client_app", sa.String(), nullable=True))
            if "auth_key_fingerprint" not in columns:
                batch_op.add_column(sa.Column("auth_key_fingerprint", sa.String(), nullable=True))
            if "override_id" not in columns:
                batch_op.add_column(sa.Column("override_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_request_logs_override_id",
                    "model_overrides",
                    ["override_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

        indexes = _indexes(bind, "request_logs")
        if "idx_logs_requested_model" not in indexes:
            op.create_index("idx_logs_requested_model", "request_logs", ["requested_model"])
        if "idx_logs_client_ip" not in indexes:
            op.create_index("idx_logs_client_ip", "request_logs", ["client_ip"])
        if "idx_logs_client_app" not in indexes:
            op.create_index("idx_logs_client_app", "request_logs", ["client_app"])
        if "idx_logs_auth_key_fingerprint" not in indexes:
            op.create_index("idx_logs_auth_key_fingerprint", "request_logs", ["auth_key_fingerprint"])

    indexes = _indexes(bind, "model_overrides")
    if "idx_model_overrides_match_type_value" not in indexes:
        op.create_index("idx_model_overrides_match_type_value", "model_overrides", ["match_type", "match_value"])


def downgrade() -> None:
    # Intentionally no-op to avoid destructive table rebuilds on SQLite.
    return
