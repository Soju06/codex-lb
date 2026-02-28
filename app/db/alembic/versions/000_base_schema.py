"""create base schema

Revision ID: 000_base_schema
Revises:
Create Date: 2026-02-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "000_base_schema"
down_revision = None
branch_labels = None
depends_on = None

_ACCOUNT_STATUS_VALUES = (
    "active",
    "rate_limited",
    "quota_exceeded",
    "paused",
    "deactivated",
)


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _account_status_enum() -> sa.Enum:
    return sa.Enum(
        *_ACCOUNT_STATUS_VALUES,
        name="account_status",
        validate_strings=True,
        create_type=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    account_status = _account_status_enum()
    if bind.dialect.name == "postgresql":
        account_status.create(bind, checkfirst=True)

    if not _table_exists(bind, "accounts"):
        op.create_table(
            "accounts",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("chatgpt_account_id", sa.String(), nullable=True),
            sa.Column("email", sa.String(), nullable=False, unique=True),
            sa.Column("plan_type", sa.String(), nullable=False),
            sa.Column("access_token_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("refresh_token_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("id_token_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("last_refresh", sa.DateTime(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("status", account_status, nullable=False),
            sa.Column("deactivation_reason", sa.Text(), nullable=True),
            sa.Column("reset_at", sa.Integer(), nullable=True),
        )

    if not _table_exists(bind, "usage_history"):
        op.create_table(
            "usage_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "recorded_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("window", sa.String(), nullable=True),
            sa.Column("used_percent", sa.Float(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("reset_at", sa.Integer(), nullable=True),
            sa.Column("window_minutes", sa.Integer(), nullable=True),
            sa.Column("credits_has", sa.Boolean(), nullable=True),
            sa.Column("credits_unlimited", sa.Boolean(), nullable=True),
            sa.Column("credits_balance", sa.Float(), nullable=True),
        )
    usage_indexes = _indexes(bind, "usage_history")
    if "idx_usage_recorded_at" not in usage_indexes:
        op.create_index("idx_usage_recorded_at", "usage_history", ["recorded_at"], unique=False)
    if "idx_usage_account_time" not in usage_indexes:
        op.create_index("idx_usage_account_time", "usage_history", ["account_id", "recorded_at"], unique=False)

    if not _table_exists(bind, "request_logs"):
        op.create_table(
            "request_logs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("request_id", sa.String(), nullable=False),
            sa.Column(
                "requested_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=True),
            sa.Column("output_tokens", sa.Integer(), nullable=True),
            sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=True),
            sa.Column("reasoning_effort", sa.String(), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("error_code", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
        )
    request_log_indexes = _indexes(bind, "request_logs")
    if "idx_logs_account_time" not in request_log_indexes:
        op.create_index("idx_logs_account_time", "request_logs", ["account_id", "requested_at"], unique=False)

    if not _table_exists(bind, "sticky_sessions"):
        op.create_table(
            "sticky_sessions",
            sa.Column("key", sa.String(), primary_key=True),
            sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )
    sticky_indexes = _indexes(bind, "sticky_sessions")
    if "idx_sticky_account" not in sticky_indexes:
        op.create_index("idx_sticky_account", "sticky_sessions", ["account_id"], unique=False)

    if not _table_exists(bind, "dashboard_settings"):
        op.create_table(
            "dashboard_settings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("sticky_threads_enabled", sa.Boolean(), nullable=False),
            sa.Column("prefer_earlier_reset_accounts", sa.Boolean(), nullable=False),
            sa.Column("totp_required_on_login", sa.Boolean(), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=True),
            sa.Column("api_key_auth_enabled", sa.Boolean(), nullable=False),
            sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("totp_last_verified_step", sa.Integer(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
        )

    if not _table_exists(bind, "api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("key_hash", sa.String(), nullable=False, unique=True),
            sa.Column("key_prefix", sa.String(), nullable=False),
            sa.Column("allowed_models", sa.Text(), nullable=True),
            sa.Column("weekly_token_limit", sa.Integer(), nullable=True),
            sa.Column("weekly_tokens_used", sa.Integer(), nullable=False),
            sa.Column("weekly_reset_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("last_used_at", sa.DateTime(), nullable=True),
        )
    api_key_indexes = _indexes(bind, "api_keys")
    if "idx_api_keys_hash" not in api_key_indexes:
        op.create_index("idx_api_keys_hash", "api_keys", ["key_hash"], unique=False)

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
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.UniqueConstraint("match_type", "match_value", name="uq_model_overrides_match"),
        )
    model_override_indexes = _indexes(bind, "model_overrides")
    if "idx_model_overrides_match_type_value" not in model_override_indexes:
        op.create_index(
            "idx_model_overrides_match_type_value",
            "model_overrides",
            ["match_type", "match_value"],
            unique=False,
        )

    request_log_columns = {column["name"] for column in sa.inspect(bind).get_columns("request_logs")}
    with op.batch_alter_table("request_logs") as batch_op:
        if "requested_model" not in request_log_columns:
            batch_op.add_column(sa.Column("requested_model", sa.String(), nullable=True))
        if "client_ip" not in request_log_columns:
            batch_op.add_column(sa.Column("client_ip", sa.String(), nullable=True))
        if "client_app" not in request_log_columns:
            batch_op.add_column(sa.Column("client_app", sa.String(), nullable=True))
        if "auth_key_fingerprint" not in request_log_columns:
            batch_op.add_column(sa.Column("auth_key_fingerprint", sa.String(), nullable=True))
        if "override_id" not in request_log_columns:
            batch_op.add_column(sa.Column("override_id", sa.Integer(), nullable=True))
            batch_op.create_foreign_key(
                "fk_request_logs_override_id",
                "model_overrides",
                ["override_id"],
                ["id"],
                ondelete="SET NULL",
            )

    request_log_indexes = _indexes(bind, "request_logs")
    if "idx_logs_requested_model" not in request_log_indexes:
        op.create_index("idx_logs_requested_model", "request_logs", ["requested_model"], unique=False)
    if "idx_logs_client_ip" not in request_log_indexes:
        op.create_index("idx_logs_client_ip", "request_logs", ["client_ip"], unique=False)
    if "idx_logs_client_app" not in request_log_indexes:
        op.create_index("idx_logs_client_app", "request_logs", ["client_app"], unique=False)
    if "idx_logs_auth_key_fingerprint" not in request_log_indexes:
        op.create_index("idx_logs_auth_key_fingerprint", "request_logs", ["auth_key_fingerprint"], unique=False)

    dashboard_columns = {column["name"] for column in sa.inspect(bind).get_columns("dashboard_settings")}
    with op.batch_alter_table("dashboard_settings") as batch_op:
        if "global_model_force_enabled" not in dashboard_columns:
            batch_op.add_column(
                sa.Column(
                    "global_model_force_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("0"),
                )
            )
        if "global_model_force_model" not in dashboard_columns:
            batch_op.add_column(sa.Column("global_model_force_model", sa.String(), nullable=True))
        if "global_model_force_reasoning_effort" not in dashboard_columns:
            batch_op.add_column(sa.Column("global_model_force_reasoning_effort", sa.String(), nullable=True))

    if not _table_exists(bind, "response_context"):
        op.create_table(
            "response_context",
            sa.Column("response_id", sa.String(), primary_key=True, nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("output_json", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
    response_context_indexes = _indexes(bind, "response_context")
    if "idx_response_context_api_key" not in response_context_indexes:
        op.create_index("idx_response_context_api_key", "response_context", ["api_key_id"], unique=False)
    if "idx_response_context_expires_at" not in response_context_indexes:
        op.create_index("idx_response_context_expires_at", "response_context", ["expires_at"], unique=False)

    if not _table_exists(bind, "response_context_items"):
        op.create_table(
            "response_context_items",
            sa.Column("item_id", sa.String(), primary_key=True, nullable=False),
            sa.Column(
                "response_id",
                sa.String(),
                sa.ForeignKey("response_context.response_id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("item_json", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(),
                nullable=False,
                server_default=sa.text("CURRENT_TIMESTAMP"),
            ),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
    response_context_item_indexes = _indexes(bind, "response_context_items")
    if "idx_response_context_items_response_id" not in response_context_item_indexes:
        op.create_index(
            "idx_response_context_items_response_id",
            "response_context_items",
            ["response_id"],
            unique=False,
        )
    if "idx_response_context_items_api_key" not in response_context_item_indexes:
        op.create_index(
            "idx_response_context_items_api_key",
            "response_context_items",
            ["api_key_id"],
            unique=False,
        )
    if "idx_response_context_items_expires_at" not in response_context_item_indexes:
        op.create_index(
            "idx_response_context_items_expires_at",
            "response_context_items",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    for table_name in (
        "model_overrides",
        "api_keys",
        "dashboard_settings",
        "response_context_items",
        "response_context",
        "sticky_sessions",
        "request_logs",
        "usage_history",
        "accounts",
    ):
        if table_name in tables:
            op.drop_table(table_name)

    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP TYPE IF EXISTS account_status"))
