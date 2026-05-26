"""add per-account SOCKS5 proxy columns to accounts

Revision ID: 20260523_000000_add_accounts_proxy_columns
Revises: 20260513_000000_add_accounts_alias
Create Date: 2026-05-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260523_000000_add_accounts_proxy_columns"
down_revision = "20260513_000000_add_accounts_alias"
branch_labels = None
depends_on = None


_NEW_COLUMNS: tuple[tuple[str, sa.Column], ...] = (
    ("proxy_host", sa.Column("proxy_host", sa.String(), nullable=True)),
    ("proxy_port", sa.Column("proxy_port", sa.Integer(), nullable=True)),
    ("proxy_username", sa.Column("proxy_username", sa.String(), nullable=True)),
    (
        "proxy_password_encrypted",
        sa.Column("proxy_password_encrypted", sa.LargeBinary(), nullable=True),
    ),
    (
        "proxy_remote_dns",
        sa.Column(
            "proxy_remote_dns",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    ),
    ("proxy_label", sa.Column("proxy_label", sa.String(), nullable=True)),
    (
        "proxy_last_validated_at",
        sa.Column("proxy_last_validated_at", sa.DateTime(), nullable=True),
    ),
)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if not columns:
        return

    missing = [(name, column) for name, column in _NEW_COLUMNS if name not in columns]
    if not missing:
        return

    with op.batch_alter_table("accounts") as batch_op:
        for _, column in missing:
            batch_op.add_column(column)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind, "accounts")
    if not columns:
        return

    present = [name for name, _ in _NEW_COLUMNS if name in columns]
    if not present:
        return

    with op.batch_alter_table("accounts") as batch_op:
        for name in reversed(present):
            batch_op.drop_column(name)
