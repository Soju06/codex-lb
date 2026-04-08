"""Align OpenAI Platform identity status with the shared account_status enum.

Revision ID: 20260408_030000_align_platform_identity_status_enum
Revises: 20260408_020000_merge_platform_fallback_and_import_heads
Create Date: 2026-04-08 08:03:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260408_030000_align_platform_identity_status_enum"
down_revision = "20260408_020000_merge_platform_fallback_and_import_heads"
branch_labels = None
depends_on = None

_ACCOUNT_STATUS_VALUES = (
    "active",
    "rate_limited",
    "quota_exceeded",
    "paused",
    "deactivated",
)


def _account_status_enum() -> sa.Enum:
    return sa.Enum(*_ACCOUNT_STATUS_VALUES, name="account_status")


def upgrade() -> None:
    bind = op.get_bind()
    account_status_enum = _account_status_enum()

    if bind.dialect.name == "postgresql":
        account_status_enum.create(bind, checkfirst=True)
        op.alter_column(
            "openai_platform_identities",
            "status",
            existing_type=sa.String(),
            existing_nullable=False,
            existing_server_default=sa.text("'active'"),
            server_default=None,
        )
        op.execute(
            sa.text(
                """
                ALTER TABLE openai_platform_identities
                ALTER COLUMN status TYPE account_status
                USING status::account_status
                """
            )
        )
        op.alter_column(
            "openai_platform_identities",
            "status",
            existing_type=account_status_enum,
            existing_nullable=False,
            server_default=sa.text("'active'"),
        )
        return

    with op.batch_alter_table("openai_platform_identities") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=sa.String(),
            type_=account_status_enum,
            existing_nullable=False,
            existing_server_default=sa.text("'active'"),
            server_default=sa.text("'active'"),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        op.alter_column(
            "openai_platform_identities",
            "status",
            existing_type=_account_status_enum(),
            existing_nullable=False,
            existing_server_default=sa.text("'active'"),
            server_default=None,
        )
        op.execute(
            sa.text(
                """
                ALTER TABLE openai_platform_identities
                ALTER COLUMN status TYPE VARCHAR
                USING status::text
                """
            )
        )
        op.alter_column(
            "openai_platform_identities",
            "status",
            existing_type=sa.String(),
            existing_nullable=False,
            server_default=sa.text("'active'"),
        )
        return

    with op.batch_alter_table("openai_platform_identities") as batch_op:
        batch_op.alter_column(
            "status",
            existing_type=_account_status_enum(),
            type_=sa.String(),
            existing_nullable=False,
            existing_server_default=sa.text("'active'"),
            server_default=sa.text("'active'"),
        )
