"""Add dashboard_roles and dashboard_role_grants with the five preset rows.

Revision ID: 20260909_000000_add_dashboard_roles
Revises: 20260908_000000_add_guest_session_generation
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.modules.dashboard_roles.seed import seed_preset_dashboard_roles

revision = "20260909_000000_add_dashboard_roles"
down_revision = "20260908_000000_add_guest_session_generation"
branch_labels = None
depends_on = None


def _roles_table(metadata: sa.MetaData) -> sa.Table:
    return sa.Table(
        "dashboard_roles",
        metadata,
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(length=32), nullable=False, unique=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("assignable_to_users", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "cloned_from_role_id",
            sa.String(),
            sa.ForeignKey("dashboard_roles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("permissions_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    metadata = sa.MetaData()
    roles = _roles_table(metadata)
    if not inspector.has_table("dashboard_roles"):
        roles.create(bind)
    if not inspector.has_table("dashboard_role_grants"):
        op.create_table(
            "dashboard_role_grants",
            sa.Column(
                "role_id",
                sa.String(),
                sa.ForeignKey("dashboard_roles.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("permission", sa.String(length=64), primary_key=True),
            sa.Column("scope", sa.String(length=8), nullable=False),
        )
    # Outside the table guard so a re-run after a partial failure still seeds.
    seed_preset_dashboard_roles(bind, roles)


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("dashboard_role_grants"):
        op.drop_table("dashboard_role_grants")
    if inspector.has_table("dashboard_roles"):
        op.drop_table("dashboard_roles")
