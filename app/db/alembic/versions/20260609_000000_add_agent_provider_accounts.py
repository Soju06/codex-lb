"""add agent provider account storage

Revision ID: 20260609_000000_add_agent_provider_accounts
Revises: 20260607_000000_merge_weekly_monthly_useragent_heads
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260609_000000_add_agent_provider_accounts"
down_revision = "20260607_000000_merge_weekly_monthly_useragent_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("agent_provider_accounts"):
        op.create_table(
            "agent_provider_accounts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("provider_id", sa.String(), nullable=False),
            sa.Column("external_account_id", sa.String(), nullable=True),
            sa.Column("display_name", sa.String(), nullable=False),
            sa.Column("status", sa.String(), server_default=sa.text("'active'"), nullable=False),
            sa.Column("auth_mode", sa.String(), nullable=False),
            sa.Column("api_key_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("credential_fingerprint", sa.String(), nullable=True),
            sa.Column("project_id", sa.String(), nullable=True),
            sa.Column("location", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("provider_id", "external_account_id", name="uq_agent_provider_accounts_external"),
            sa.UniqueConstraint("provider_id", "credential_fingerprint", name="uq_agent_provider_accounts_credential"),
        )
        op.create_index(
            "idx_agent_provider_accounts_provider_status",
            "agent_provider_accounts",
            ["provider_id", "status", "display_name"],
        )
    if not inspector.has_table("agent_provider_quota_windows"):
        op.create_table(
            "agent_provider_quota_windows",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("account_id", sa.String(), nullable=False),
            sa.Column("dimension", sa.String(), nullable=False),
            sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("limit", sa.Integer(), nullable=True),
            sa.Column("reset_at", sa.DateTime(), nullable=True),
            sa.Column("recorded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["account_id"], ["agent_provider_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("account_id", "dimension", name="uq_agent_provider_quota_windows_account_dimension"),
        )
        op.create_index(
            "idx_agent_provider_quota_windows_account_dimension",
            "agent_provider_quota_windows",
            ["account_id", "dimension"],
        )
    if not inspector.has_table("agent_provider_routing_settings"):
        op.create_table(
            "agent_provider_routing_settings",
            sa.Column("provider_id", sa.String(), nullable=False),
            sa.Column("strategy", sa.String(), server_default=sa.text("'capacity_weighted'"), nullable=False),
            sa.Column("single_account_id", sa.String(), nullable=True),
            sa.Column("quota_threshold_pct", sa.Float(), server_default=sa.text("100.0"), nullable=False),
            sa.Column("round_robin_cursor", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["single_account_id"], ["agent_provider_accounts.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("provider_id"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("agent_provider_routing_settings"):
        op.drop_table("agent_provider_routing_settings")
    if inspector.has_table("agent_provider_quota_windows"):
        op.drop_index("idx_agent_provider_quota_windows_account_dimension", table_name="agent_provider_quota_windows")
        op.drop_table("agent_provider_quota_windows")
    if inspector.has_table("agent_provider_accounts"):
        op.drop_index("idx_agent_provider_accounts_provider_status", table_name="agent_provider_accounts")
        op.drop_table("agent_provider_accounts")
