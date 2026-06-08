"""add quota planner tables

Revision ID: 20260520_030000_add_quota_planner
Revises: 20260520_000000_merge_api_key_and_http_bridge_heads
Create Date: 2026-05-20
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260520_030000_add_quota_planner"
down_revision = "20260520_000000_merge_api_key_and_http_bridge_heads"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name")}


def _ensure_request_logs_request_kind() -> None:
    if "request_logs" not in _table_names():
        return

    request_log_columns = _column_names("request_logs")
    if "request_kind" not in request_log_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(
                sa.Column("request_kind", sa.String(), nullable=True, server_default=sa.text("'normal'"))
            )

    op.execute(
        sa.text(
            """
            UPDATE request_logs
            SET request_kind = 'normal'
            WHERE request_kind IS NULL OR request_kind = ''
            """
        )
    )
    if "source" in request_log_columns:
        op.execute(
            sa.text(
                """
                UPDATE request_logs
                SET request_kind = 'warmup'
                WHERE source = 'limit_warmup'
                """
            )
        )
    with op.batch_alter_table("request_logs") as batch_op:
        batch_op.alter_column(
            "request_kind",
            existing_type=sa.String(),
            nullable=False,
            server_default=sa.text("'normal'"),
        )


def upgrade() -> None:
    tables = _table_names()
    _ensure_request_logs_request_kind()

    if "quota_planner_settings" not in tables:
        op.create_table(
            "quota_planner_settings",
            sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
            sa.Column("mode", sa.String(), server_default=sa.text("'shadow'"), nullable=False),
            sa.Column("timezone", sa.String(), server_default=sa.text("'UTC'"), nullable=False),
            sa.Column("working_days_json", sa.Text(), server_default=sa.text("'[0,1,2,3,4]'"), nullable=False),
            sa.Column("working_hours_start", sa.String(), server_default=sa.text("'09:00'"), nullable=False),
            sa.Column("working_hours_end", sa.String(), server_default=sa.text("'18:00'"), nullable=False),
            sa.Column("prewarm_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("prewarm_lead_minutes", sa.Integer(), server_default=sa.text("300"), nullable=False),
            sa.Column("max_warmups_per_day", sa.Integer(), server_default=sa.text("3"), nullable=False),
            sa.Column("max_warmup_credits_per_day", sa.Float(), server_default=sa.text("0.0"), nullable=False),
            sa.Column("min_expected_gain", sa.Float(), server_default=sa.text("1.0"), nullable=False),
            sa.Column("forecast_quantile", sa.String(), server_default=sa.text("'p75'"), nullable=False),
            sa.Column("allow_synthetic_traffic", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("warmup_model_preference", sa.String(), nullable=True),
            sa.Column("dry_run", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    tables = _table_names()
    if "quota_planner_decisions" not in tables:
        op.create_table(
            "quota_planner_decisions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("action", sa.String(), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            sa.Column("score", sa.Float(), server_default=sa.text("0.0"), nullable=False),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("forecast_snapshot_hash", sa.String(length=64), nullable=True),
            sa.Column("state_before_json", sa.Text(), nullable=True),
            sa.Column("state_after_json", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), server_default=sa.text("'planned'"), nullable=False),
            sa.Column("idempotency_key", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("idempotency_key"),
        )

    if "quota_window_observations" not in tables:
        op.create_table(
            "quota_window_observations",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("observed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("primary_remaining_percent", sa.Float(), nullable=True),
            sa.Column("primary_reset_at", sa.Integer(), nullable=True),
            sa.Column("secondary_remaining_percent", sa.Float(), nullable=True),
            sa.Column("secondary_reset_at", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.Column("confidence", sa.String(), server_default=sa.text("'unknown'"), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    request_log_indexes = _index_names("request_logs")
    if "idx_logs_request_kind_time" not in request_log_indexes:
        op.create_index(
            "idx_logs_request_kind_time",
            "request_logs",
            ["request_kind", sa.text("requested_at DESC"), sa.text("id DESC")],
            unique=False,
        )

    decision_indexes = _index_names("quota_planner_decisions")
    if "idx_quota_planner_decisions_status_created" not in decision_indexes:
        op.create_index(
            "idx_quota_planner_decisions_status_created",
            "quota_planner_decisions",
            ["status", sa.text("created_at DESC")],
            unique=False,
        )
    if "idx_quota_planner_decisions_account_created" not in decision_indexes:
        op.create_index(
            "idx_quota_planner_decisions_account_created",
            "quota_planner_decisions",
            ["account_id", sa.text("created_at DESC")],
            unique=False,
        )

    observation_indexes = _index_names("quota_window_observations")
    if "idx_quota_window_observations_account_time" not in observation_indexes:
        op.create_index(
            "idx_quota_window_observations_account_time",
            "quota_window_observations",
            ["account_id", sa.text("observed_at DESC")],
            unique=False,
        )


def downgrade() -> None:
    if "idx_quota_window_observations_account_time" in _index_names("quota_window_observations"):
        op.drop_index("idx_quota_window_observations_account_time", table_name="quota_window_observations")
    if "idx_quota_planner_decisions_account_created" in _index_names("quota_planner_decisions"):
        op.drop_index("idx_quota_planner_decisions_account_created", table_name="quota_planner_decisions")
    if "idx_quota_planner_decisions_status_created" in _index_names("quota_planner_decisions"):
        op.drop_index("idx_quota_planner_decisions_status_created", table_name="quota_planner_decisions")
    if "idx_logs_request_kind_time" in _index_names("request_logs"):
        op.drop_index("idx_logs_request_kind_time", table_name="request_logs")

    tables = _table_names()
    if "quota_window_observations" in tables:
        op.drop_table("quota_window_observations")
    if "quota_planner_decisions" in tables:
        op.drop_table("quota_planner_decisions")
    if "quota_planner_settings" in tables:
        op.drop_table("quota_planner_settings")
