"""split sticky session primary key by kind

Revision ID: 20260312_000000_split_sticky_sessions_primary_key_by_kind
Revises: 20260310_120000_add_sticky_session_kinds_and_affinity_ttl
Create Date: 2026-03-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260312_000000_split_sticky_sessions_primary_key_by_kind"
down_revision = "20260310_120000_add_sticky_session_kinds_and_affinity_ttl"
branch_labels = None
depends_on = None

_TABLE_NAME = "sticky_sessions"
_TEMP_TABLE_NAME = "sticky_sessions__new"


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _primary_key_columns(connection: Connection, table_name: str) -> list[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return []
    constraint = inspector.get_pk_constraint(table_name) or {}
    columns = constraint.get("constrained_columns") or []
    return [str(column) for column in columns]


def _sticky_session_kind_enum(connection: Connection, *, create_type: bool = True) -> sa.Enum:
    if connection.dialect.name == "postgresql":
        return postgresql.ENUM(
            "codex_session",
            "sticky_thread",
            "prompt_cache",
            name="sticky_session_kind",
            create_type=create_type,
        )
    return sa.Enum(
        "codex_session",
        "sticky_thread",
        "prompt_cache",
        name="sticky_session_kind",
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, _TABLE_NAME):
        return
    if _primary_key_columns(bind, _TABLE_NAME) == ["key", "kind"]:
        return

    if bind.dialect.name == "postgresql":
        _sticky_session_kind_enum(bind).create(bind, checkfirst=True)

    op.create_table(
        _TEMP_TABLE_NAME,
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column(
            "kind",
            _sticky_session_kind_enum(bind, create_type=False),
            primary_key=True,
            nullable=False,
            server_default=sa.text("'sticky_thread'"),
        ),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    # Dialect-safe row copy: ``key`` is a reserved word on MySQL, so build the
    # statement with SQLAlchemy so each dialect quotes identifiers correctly.
    copy_columns = ["key", "kind", "account_id", "created_at", "updated_at"]
    source = sa.table(_TABLE_NAME, *(sa.column(column) for column in copy_columns))
    target = sa.table(_TEMP_TABLE_NAME, *(sa.column(column) for column in copy_columns))
    op.execute(target.insert().from_select(copy_columns, sa.select(*(source.c[column] for column in copy_columns))))
    op.drop_table(_TABLE_NAME)
    op.rename_table(_TEMP_TABLE_NAME, _TABLE_NAME)
    op.create_index("idx_sticky_account", _TABLE_NAME, ["account_id"], unique=False)
    op.execute(sa.text("CREATE INDEX idx_sticky_kind_updated_at ON sticky_sessions (kind, updated_at DESC)"))


def downgrade() -> None:
    return
