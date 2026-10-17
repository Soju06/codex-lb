"""add versioned HTTP bridge event chunks

Revision ID: 20260826_000000_add_http_bridge_event_chunks
Revises: 20260821_000000_add_retry_circuit_admission_generation
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260826_000000_add_http_bridge_event_chunks"
down_revision = "20260821_000000_add_retry_circuit_admission_generation"
branch_labels = None
depends_on = None

_OPERATIONS_TABLE = "http_bridge_operations"
_CHUNKS_TABLE = "http_bridge_operation_event_chunks"
_FORMAT_COLUMN = "spool_format"
_ROWS_V1 = "rows_v1"
_CHUNKS_V2 = "chunks_v2"


def _has_table(connection: Connection, table_name: str) -> bool:
    return sa.inspect(connection).has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    if not _has_table(connection, table_name):
        return set()
    return {str(column["name"]) for column in sa.inspect(connection).get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _OPERATIONS_TABLE) and _FORMAT_COLUMN not in _columns(bind, _OPERATIONS_TABLE):
        op.add_column(
            _OPERATIONS_TABLE,
            sa.Column(
                _FORMAT_COLUMN,
                sa.String(16),
                nullable=False,
                server_default=sa.text(f"'{_ROWS_V1}'"),
            ),
        )
    if _has_table(bind, _CHUNKS_TABLE):
        return
    op.create_table(
        _CHUNKS_TABLE,
        sa.Column("operation_id", sa.String(80), nullable=False),
        sa.Column("first_sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("codec", sa.String(64), nullable=False),
        sa.Column("uncompressed_bytes", sa.Integer(), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "first_sequence_number > 0",
            name="ck_http_bridge_event_chunks_first_sequence_positive",
        ),
        sa.CheckConstraint("event_count > 0", name="ck_http_bridge_event_chunks_event_count_positive"),
        sa.CheckConstraint(
            "uncompressed_bytes >= 0",
            name="ck_http_bridge_event_chunks_bytes_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            [f"{_OPERATIONS_TABLE}.operation_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("operation_id", "first_sequence_number"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _CHUNKS_TABLE):
        chunk_exists = bind.execute(sa.text(f"SELECT 1 FROM {_CHUNKS_TABLE} LIMIT 1")).first() is not None
        if chunk_exists:
            raise RuntimeError("cannot downgrade while durable transcript chunks exist")
    if _FORMAT_COLUMN in _columns(bind, _OPERATIONS_TABLE):
        v2_operation_exists = (
            bind.execute(
                sa.text(f"SELECT 1 FROM {_OPERATIONS_TABLE} WHERE {_FORMAT_COLUMN} = :spool_format LIMIT 1"),
                {"spool_format": _CHUNKS_V2},
            ).first()
            is not None
        )
        if v2_operation_exists:
            raise RuntimeError("cannot downgrade while chunks_v2 operations exist")
    if _has_table(bind, _CHUNKS_TABLE):
        op.drop_table(_CHUNKS_TABLE)
    if _FORMAT_COLUMN in _columns(bind, _OPERATIONS_TABLE):
        if bind.dialect.name == "sqlite":
            # SQLite batch migrations recreate and drop the parent table. Keep
            # its legacy event rows from cascading away while that happens.
            bind.execute(sa.text("PRAGMA foreign_keys=OFF"))
            try:
                with op.batch_alter_table(_OPERATIONS_TABLE) as batch_op:
                    batch_op.drop_column(_FORMAT_COLUMN)
            finally:
                bind.execute(sa.text("PRAGMA foreign_keys=ON"))
        else:
            op.drop_column(_OPERATIONS_TABLE, _FORMAT_COLUMN)
