"""add retry-circuit admission generation

Revision ID: 20260821_000000_add_retry_circuit_admission_generation
Revises: 20260816_000000_add_model_source_embeddings
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_000000_add_retry_circuit_admission_generation"
down_revision = "20260816_000000_add_model_source_embeddings"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_retry_circuits"
_COLUMN = "admission_generation"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(
            sa.Column(
                _COLUMN,
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
