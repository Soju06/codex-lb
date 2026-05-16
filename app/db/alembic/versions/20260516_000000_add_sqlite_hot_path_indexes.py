"""add sqlite hot path indexes

Revision ID: 20260516_000000_add_sqlite_hot_path_indexes
Revises: 20260514_000000_add_request_logs_api_key_time_index
Create Date: 2026-05-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260516_000000_add_sqlite_hot_path_indexes"
down_revision = "20260514_000000_add_request_logs_api_key_time_index"
branch_labels = None
depends_on = None


def _index_names(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return set()
    return {name for index in inspector.get_indexes(table_name) if isinstance(name := index["name"], str)}


def upgrade() -> None:
    reservation_indexes = _index_names("api_key_usage_reservations")
    if "idx_api_key_usage_reservations_status_updated_at" not in reservation_indexes:
        op.create_index(
            "idx_api_key_usage_reservations_status_updated_at",
            "api_key_usage_reservations",
            ["status", "updated_at"],
            unique=False,
        )

    bridge_indexes = _index_names("http_bridge_sessions")
    if "idx_http_bridge_sessions_latest_turn_scope_state_seen" not in bridge_indexes:
        op.create_index(
            "idx_http_bridge_sessions_latest_turn_scope_state_seen",
            "http_bridge_sessions",
            [
                "latest_turn_state",
                "api_key_scope",
                "state",
                sa.text("last_seen_at DESC"),
                sa.text("updated_at DESC"),
            ],
            unique=False,
        )
    if "idx_http_bridge_sessions_latest_response_scope_state_seen" not in bridge_indexes:
        op.create_index(
            "idx_http_bridge_sessions_latest_response_scope_state_seen",
            "http_bridge_sessions",
            [
                "latest_response_id",
                "api_key_scope",
                "state",
                sa.text("last_seen_at DESC"),
                sa.text("updated_at DESC"),
            ],
            unique=False,
        )


def downgrade() -> None:
    bridge_indexes = _index_names("http_bridge_sessions")
    if "idx_http_bridge_sessions_latest_response_scope_state_seen" in bridge_indexes:
        op.drop_index("idx_http_bridge_sessions_latest_response_scope_state_seen", table_name="http_bridge_sessions")
    if "idx_http_bridge_sessions_latest_turn_scope_state_seen" in bridge_indexes:
        op.drop_index("idx_http_bridge_sessions_latest_turn_scope_state_seen", table_name="http_bridge_sessions")

    reservation_indexes = _index_names("api_key_usage_reservations")
    if "idx_api_key_usage_reservations_status_updated_at" in reservation_indexes:
        op.drop_index("idx_api_key_usage_reservations_status_updated_at", table_name="api_key_usage_reservations")
