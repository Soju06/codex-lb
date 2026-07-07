"""add model source tables

Revision ID: 20260702_000000_add_model_sources
Revises: 20260701_000000_add_weekly_pace_smoothing_minutes
Create Date: 2026-07-02 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260702_000000_add_model_sources"
down_revision = "20260701_000000_add_weekly_pace_smoothing_minutes"
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
    return {name for index in sa.inspect(connection).get_indexes(table_name) if (name := index.get("name"))}


def _foreign_keys(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {name for fk in sa.inspect(connection).get_foreign_keys(table_name) if (name := fk.get("name"))}


def _create_index_if_missing(
    connection: Connection,
    index_name: str,
    table_name: str,
    columns: list[str],
) -> None:
    if not _has_table(connection, table_name):
        return
    if index_name not in _indexes(connection, table_name):
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    bind = op.get_bind()

    api_key_columns = _columns(bind, "api_keys")
    if api_key_columns and "source_assignment_scope_enabled" not in api_key_columns:
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "source_assignment_scope_enabled",
                    sa.Boolean(),
                    server_default=sa.false(),
                    nullable=False,
                )
            )

    if not _has_table(bind, "model_sources"):
        op.create_table(
            "model_sources",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), server_default=sa.text("'openai_compatible'"), nullable=False),
            sa.Column("base_url", sa.String(), nullable=False),
            sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("health_status", sa.String(), server_default=sa.text("'unknown'"), nullable=False),
            sa.Column("supports_chat_completions", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("supports_responses", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("timeout_seconds", sa.Integer(), nullable=True),
            sa.Column("max_concurrency", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _has_table(bind, "model_source_models"):
        op.create_table(
            "model_source_models",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=True),
            sa.Column("context_window", sa.Integer(), nullable=True),
            sa.Column("max_output_tokens", sa.Integer(), nullable=True),
            sa.Column("supports_streaming", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("supports_tools", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("supports_vision", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("input_per_1m", sa.Float(), nullable=True),
            sa.Column("cached_input_per_1m", sa.Float(), nullable=True),
            sa.Column("output_per_1m", sa.Float(), nullable=True),
            sa.Column("raw_metadata_json", sa.Text(), nullable=True),
            sa.Column("is_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["source_id"], ["model_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_id", "model", name="uq_model_source_models_source_model"),
        )

    if not _has_table(bind, "api_key_model_sources"):
        op.create_table(
            "api_key_model_sources",
            sa.Column("api_key_id", sa.String(), nullable=False),
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_id"], ["model_sources.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("api_key_id", "source_id"),
        )

    request_log_columns = _columns(bind, "request_logs")
    if request_log_columns and "model_source_id" not in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("model_source_id", sa.String(), nullable=True))
    request_log_columns = _columns(bind, "request_logs")
    if request_log_columns and "model_source_kind" not in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("model_source_kind", sa.String(), nullable=True))

    _create_index_if_missing(bind, "idx_logs_model_source_time", "request_logs", ["model_source_id", "requested_at"])
    _create_index_if_missing(
        bind,
        "idx_api_key_model_sources_source_id",
        "api_key_model_sources",
        ["source_id"],
    )
    _create_index_if_missing(
        bind,
        "idx_model_source_models_model_enabled",
        "model_source_models",
        ["model", "is_enabled"],
    )


def downgrade() -> None:
    bind = op.get_bind()

    if "idx_api_key_model_sources_source_id" in _indexes(bind, "api_key_model_sources"):
        op.drop_index("idx_api_key_model_sources_source_id", table_name="api_key_model_sources")
    if "idx_logs_model_source_time" in _indexes(bind, "request_logs"):
        op.drop_index("idx_logs_model_source_time", table_name="request_logs")
    if "idx_model_source_models_model_enabled" in _indexes(bind, "model_source_models"):
        op.drop_index("idx_model_source_models_model_enabled", table_name="model_source_models")

    request_log_columns = _columns(bind, "request_logs")
    if "model_source_kind" in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("model_source_kind")
    request_log_columns = _columns(bind, "request_logs")
    if "model_source_id" in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("model_source_id")

    if _has_table(bind, "api_key_model_sources"):
        op.drop_table("api_key_model_sources")
    if _has_table(bind, "model_source_models"):
        op.drop_table("model_source_models")
    if _has_table(bind, "model_sources"):
        op.drop_table("model_sources")

    api_key_columns = _columns(bind, "api_keys")
    if "source_assignment_scope_enabled" in api_key_columns:
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.drop_column("source_assignment_scope_enabled")
