"""add durable http bridge session tables

Revision ID: 20260409_000000_add_http_bridge_sessions
Revises: 20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads
Create Date: 2026-04-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260409_000000_add_http_bridge_sessions"
down_revision = "20260407_010000_merge_api_key_assignment_and_bridge_gateway_heads"
branch_labels = None
depends_on = None


_HTTP_BRIDGE_SESSION_STATE = sa.Enum(
    "active",
    "draining",
    "closed",
    name="http_bridge_session_state",
)


def _has_table(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "http_bridge_sessions"):
        op.create_table(
            "http_bridge_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_key_kind", sa.String(64), nullable=False),
            sa.Column("session_key_value", sa.Text(), nullable=False),
            sa.Column("session_key_hash", sa.String(64), nullable=False),
            sa.Column("api_key_scope", sa.String(255), nullable=False),
            sa.Column("owner_instance_id", sa.String(255), nullable=True),
            sa.Column("owner_epoch", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("state", _HTTP_BRIDGE_SESSION_STATE, nullable=False, server_default="active"),
            sa.Column("account_id", sa.String(), nullable=True),
            sa.Column("model", sa.String(), nullable=True),
            sa.Column("service_tier", sa.String(), nullable=True),
            sa.Column("latest_turn_state", sa.Text(), nullable=True),
            sa.Column("latest_response_id", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["account_id"],
                ["accounts.id"],
                ondelete="SET NULL",
            ),
            sa.UniqueConstraint(
                "session_key_kind",
                "session_key_hash",
                "api_key_scope",
                name="uq_http_bridge_sessions_session_key",
            ),
        )
        op.create_index(
            "idx_http_bridge_sessions_owner_state",
            "http_bridge_sessions",
            ["owner_instance_id", "state"],
        )
        op.create_index(
            "idx_http_bridge_sessions_lease",
            "http_bridge_sessions",
            ["lease_expires_at"],
        )
        op.create_index(
            "idx_http_bridge_sessions_last_seen",
            "http_bridge_sessions",
            ["last_seen_at"],
        )

    if not _has_table(bind, "http_bridge_session_aliases"):
        op.create_table(
            "http_bridge_session_aliases",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("session_id", sa.String(36), nullable=False),
            sa.Column("alias_kind", sa.String(64), nullable=False),
            sa.Column("alias_value", sa.Text(), nullable=False),
            sa.Column("alias_hash", sa.String(64), nullable=False),
            sa.Column("api_key_scope", sa.String(255), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["http_bridge_sessions.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "alias_kind",
                "alias_hash",
                "api_key_scope",
                name="uq_http_bridge_session_aliases_alias",
            ),
        )
        op.create_index(
            "idx_http_bridge_session_aliases_session_id",
            "http_bridge_session_aliases",
            ["session_id"],
        )
        op.create_index(
            "idx_http_bridge_session_aliases_alias_kind_hash_scope",
            "http_bridge_session_aliases",
            ["alias_kind", "alias_hash", "api_key_scope"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "http_bridge_session_aliases"):
        op.drop_index("idx_http_bridge_session_aliases_alias_kind_hash_scope", table_name="http_bridge_session_aliases")
        op.drop_index("idx_http_bridge_session_aliases_session_id", table_name="http_bridge_session_aliases")
        op.drop_table("http_bridge_session_aliases")
    if _has_table(bind, "http_bridge_sessions"):
        op.drop_index("idx_http_bridge_sessions_last_seen", table_name="http_bridge_sessions")
        op.drop_index("idx_http_bridge_sessions_lease", table_name="http_bridge_sessions")
        op.drop_index("idx_http_bridge_sessions_owner_state", table_name="http_bridge_sessions")
        op.drop_table("http_bridge_sessions")
    _HTTP_BRIDGE_SESSION_STATE.drop(bind, checkfirst=True)
