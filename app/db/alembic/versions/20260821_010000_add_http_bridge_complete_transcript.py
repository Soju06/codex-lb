"""store complete HTTP bridge transcript output items

Revision ID: 20260821_010000_add_http_bridge_complete_transcript
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.alembic.http_bridge_migration_ownership import (
    drop_ownership_table_if_empty,
    ensure_ownership_table,
    forget_created,
    mark_created,
    was_created,
)

revision = "20260821_010000_add_http_bridge_complete_transcript"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None

_TABLE = "http_bridge_operations"
_INDEX = "idx_http_bridge_operations_response_state"


def _columns(bind) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if not columns:
        return
    ensure_ownership_table(bind)
    created_columns: list[str] = []
    with op.batch_alter_table(_TABLE) as batch_op:
        if "transcript_version" not in columns:
            created_columns.append("transcript_version")
            batch_op.add_column(
                sa.Column("transcript_version", sa.Integer(), nullable=False, server_default=sa.text("0"))
            )
        if "response_output_items_json" not in columns:
            created_columns.append("response_output_items_json")
            batch_op.add_column(sa.Column("response_output_items_json", sa.Text(), nullable=True))
        if "response_output_items_complete" not in columns:
            created_columns.append("response_output_items_complete")
            batch_op.add_column(
                sa.Column(
                    "response_output_items_complete",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                )
            )
    for column in created_columns:
        mark_created(bind, revision, "column", column)
    inspector = sa.inspect(bind)
    indexes = {str(index["name"]) for index in inspector.get_indexes(_TABLE)}
    if _INDEX not in indexes:
        op.create_index(_INDEX, _TABLE, ["response_id", "state"], unique=False)
        mark_created(bind, revision, "index", _INDEX)


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if columns:
        columns_to_drop = [
            column
            for column in ("response_output_items_complete", "response_output_items_json", "transcript_version")
            if column in columns and was_created(bind, revision, "column", column)
        ]
        if columns_to_drop:
            with op.batch_alter_table(_TABLE) as batch_op:
                for column in columns_to_drop:
                    batch_op.drop_column(column)
            for column in columns_to_drop:
                forget_created(bind, revision, "column", column)
        inspector = sa.inspect(bind)
        if _INDEX in {str(index["name"]) for index in inspector.get_indexes(_TABLE)} and was_created(
            bind, revision, "index", _INDEX
        ):
            op.drop_index(_INDEX, table_name=_TABLE)
            forget_created(bind, revision, "index", _INDEX)
    drop_ownership_table_if_empty(bind)
