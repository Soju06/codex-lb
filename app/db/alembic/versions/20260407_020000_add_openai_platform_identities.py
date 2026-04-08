"""add openai platform identities

Revision ID: 20260407_020000_add_openai_platform_identities
Revises: 20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260407_020000_add_openai_platform_identities"
down_revision = "20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _row_count(connection: Connection, table_name: str) -> int:
    if not _table_exists(connection, table_name):
        return 0
    return int(connection.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0)


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "openai_platform_identities"):
        op.create_table(
            "openai_platform_identities",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("singleton_key", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("organization_id", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("eligible_route_families", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(), nullable=False, server_default="active"),
            sa.Column("last_validated_at", sa.DateTime(), nullable=True),
            sa.Column("last_auth_failure_reason", sa.Text(), nullable=True),
            sa.Column("deactivation_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.PrimaryKeyConstraint("id"),
        )
    else:
        if _row_count(bind, "openai_platform_identities") > 1:
            raise RuntimeError(
                "openai_platform_identities contains multiple rows; manual cleanup is required before "
                "applying the phase-1 singleton constraint"
            )
        identity_columns = _column_names(bind, "openai_platform_identities")
        if "singleton_key" not in identity_columns:
            op.add_column(
                "openai_platform_identities",
                sa.Column("singleton_key", sa.Integer(), nullable=False, server_default="1"),
            )

    op.execute(sa.text("UPDATE openai_platform_identities SET singleton_key = COALESCE(singleton_key, 1)"))

    request_log_columns = _column_names(bind, "request_logs")
    if "provider_kind" not in request_log_columns:
        op.add_column("request_logs", sa.Column("provider_kind", sa.String(), nullable=True))
    if "routing_subject_id" not in request_log_columns:
        op.add_column("request_logs", sa.Column("routing_subject_id", sa.String(), nullable=True))
    if "route_class" not in request_log_columns:
        op.add_column("request_logs", sa.Column("route_class", sa.String(), nullable=True))
    if "upstream_request_id" not in request_log_columns:
        op.add_column("request_logs", sa.Column("upstream_request_id", sa.String(), nullable=True))
    if "rejection_reason" not in request_log_columns:
        op.add_column("request_logs", sa.Column("rejection_reason", sa.String(), nullable=True))

    sticky_columns = _column_names(bind, "sticky_sessions")
    if "provider_kind" not in sticky_columns:
        op.add_column(
            "sticky_sessions",
            sa.Column("provider_kind", sa.String(), nullable=False, server_default="chatgpt_web"),
        )
    if "routing_subject_id" not in sticky_columns:
        op.add_column("sticky_sessions", sa.Column("routing_subject_id", sa.String(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE request_logs
            SET provider_kind = COALESCE(provider_kind, 'chatgpt_web'),
                routing_subject_id = COALESCE(routing_subject_id, account_id)
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE sticky_sessions
            SET provider_kind = COALESCE(provider_kind, 'chatgpt_web'),
                routing_subject_id = COALESCE(routing_subject_id, account_id)
            """
        )
    )

    existing_indexes = _indexes(bind, "openai_platform_identities")
    if "idx_openai_platform_identities_status" not in existing_indexes:
        op.create_index(
            "idx_openai_platform_identities_status",
            "openai_platform_identities",
            ["status"],
            unique=False,
        )
    if "idx_openai_platform_identities_label" not in existing_indexes:
        op.create_index(
            "idx_openai_platform_identities_label",
            "openai_platform_identities",
            ["label"],
            unique=False,
        )
    if "uq_openai_platform_identities_singleton" not in existing_indexes:
        op.create_index(
            "uq_openai_platform_identities_singleton",
            "openai_platform_identities",
            ["singleton_key"],
            unique=True,
        )

    log_indexes = _indexes(bind, "request_logs")
    if "idx_logs_provider_time" not in log_indexes:
        op.create_index("idx_logs_provider_time", "request_logs", ["provider_kind", "requested_at"], unique=False)
    if "idx_logs_routing_subject_time" not in log_indexes:
        op.create_index(
            "idx_logs_routing_subject_time",
            "request_logs",
            ["routing_subject_id", "requested_at"],
            unique=False,
        )

    sticky_indexes = _indexes(bind, "sticky_sessions")
    if "idx_sticky_provider_routing" not in sticky_indexes:
        op.create_index(
            "idx_sticky_provider_routing",
            "sticky_sessions",
            ["provider_kind", "routing_subject_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    sticky_indexes = _indexes(bind, "sticky_sessions")
    if "idx_sticky_provider_routing" in sticky_indexes:
        op.drop_index("idx_sticky_provider_routing", table_name="sticky_sessions")

    log_indexes = _indexes(bind, "request_logs")
    if "idx_logs_routing_subject_time" in log_indexes:
        op.drop_index("idx_logs_routing_subject_time", table_name="request_logs")
    if "idx_logs_provider_time" in log_indexes:
        op.drop_index("idx_logs_provider_time", table_name="request_logs")

    identity_indexes = _indexes(bind, "openai_platform_identities")
    if "uq_openai_platform_identities_singleton" in identity_indexes:
        op.drop_index("uq_openai_platform_identities_singleton", table_name="openai_platform_identities")
    if "idx_openai_platform_identities_label" in identity_indexes:
        op.drop_index("idx_openai_platform_identities_label", table_name="openai_platform_identities")
    if "idx_openai_platform_identities_status" in identity_indexes:
        op.drop_index("idx_openai_platform_identities_status", table_name="openai_platform_identities")

    request_log_columns = _column_names(bind, "request_logs")
    for column_name in (
        "rejection_reason",
        "upstream_request_id",
        "route_class",
        "routing_subject_id",
        "provider_kind",
    ):
        if column_name in request_log_columns:
            op.drop_column("request_logs", column_name)

    sticky_columns = _column_names(bind, "sticky_sessions")
    for column_name in ("routing_subject_id", "provider_kind"):
        if column_name in sticky_columns:
            op.drop_column("sticky_sessions", column_name)

    if _table_exists(bind, "openai_platform_identities"):
        op.drop_table("openai_platform_identities")
