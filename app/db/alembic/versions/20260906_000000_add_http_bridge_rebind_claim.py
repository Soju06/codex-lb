"""Fence operation rebind compensation with a per-attempt identity."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.alembic.http_bridge_migration_ownership import (
    ensure_ownership_table,
    forget_created,
    mark_created,
    was_created,
)

revision = "20260906_000000_add_http_bridge_rebind_claim"
down_revision = "20260904_000000_repair_http_bridge_ownership_registry"
branch_labels = None
depends_on = None
_TABLE = "http_bridge_operations"
_COLUMN = "rebind_claim_id"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns or _COLUMN in columns:
        return
    ensure_ownership_table(bind)
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.String(36), nullable=True))
    mark_created(bind, revision, "column", _COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind) and was_created(bind, revision, "column", _COLUMN):
        with op.batch_alter_table(_TABLE) as batch_op:
            batch_op.drop_column(_COLUMN)
        forget_created(bind, revision, "column", _COLUMN)
    # The parent repair owns and requires this shared registry, even when
    # it contains no markers. Only remove this revision's column and marker.
