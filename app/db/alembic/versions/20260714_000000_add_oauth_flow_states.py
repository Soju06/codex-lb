"""add oauth flow states coordination table

Revision ID: 20260714_000000_add_oauth_flow_states
Revises: 20260715_000000_add_request_log_queue_latency
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260714_000000_add_oauth_flow_states"
down_revision = "20260715_000000_add_request_log_queue_latency"
branch_labels = None
depends_on = None

_TABLE_NAME = "oauth_flow_states"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_TABLE_NAME):
        return
    op.create_table(
        _TABLE_NAME,
        sa.Column("flow_id", sa.String(), nullable=False),
        sa.Column("state_token", sa.String(), nullable=True),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("intended_account_id", sa.String(), nullable=True),
        sa.Column("code_verifier_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("device_auth_id", sa.String(), nullable=True),
        sa.Column("user_code", sa.String(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("flow_id"),
    )
    op.create_index(
        "ix_oauth_flow_states_state_token",
        _TABLE_NAME,
        ["state_token"],
        unique=True,
    )
    op.create_index(
        "ix_oauth_flow_states_created_at",
        _TABLE_NAME,
        ["created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE_NAME):
        return
    op.drop_index("ix_oauth_flow_states_created_at", table_name=_TABLE_NAME)
    op.drop_index("ix_oauth_flow_states_state_token", table_name=_TABLE_NAME)
    op.drop_table(_TABLE_NAME)
