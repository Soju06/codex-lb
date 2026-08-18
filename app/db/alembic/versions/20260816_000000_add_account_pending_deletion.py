"""add account pending-deletion marker columns

Revision ID: 20260816_000000_add_account_pending_deletion
Revises: 20260812_120000_add_sticky_abandonment_scope
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260816_000000_add_account_pending_deletion"
down_revision = "20260812_120000_add_sticky_abandonment_scope"
branch_labels = None
depends_on = None

_TABLE = "accounts"
_INDEX = "idx_accounts_delete_requested_at"


def _columns(bind) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(_TABLE)}


def _indexes(bind) -> set[str]:
    return {index["name"] for index in sa.inspect(bind).get_indexes(_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if "delete_requested_at" not in columns:
        op.add_column(_TABLE, sa.Column("delete_requested_at", sa.DateTime(), nullable=True))
    if "delete_history_requested" not in columns:
        op.add_column(
            _TABLE,
            sa.Column(
                "delete_history_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if _INDEX not in _indexes(bind):
        # Pending-deletion queue probe/order support; partial so it is empty
        # (and free) in the steady state with no pending deletions.
        op.create_index(
            _INDEX,
            _TABLE,
            ["delete_requested_at", "id"],
            postgresql_where=sa.text("delete_requested_at IS NOT NULL"),
            sqlite_where=sa.text("delete_requested_at IS NOT NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = _columns(bind)
    if "delete_requested_at" in columns:
        # The marker columns are the deletion queue's only durable state:
        # dropping them while deletions are queued would silently abandon
        # acknowledged deletions and hand the parent build unusable
        # (credential-wiped, partially drained) account rows it would list
        # again. Refuse instead — let the worker finish (or supersede the
        # deletions via re-import/reauth) before downgrading.
        pending = bind.execute(
            sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE delete_requested_at IS NOT NULL")  # noqa: S608
        ).scalar()
        if pending:
            raise RuntimeError(
                f"cannot downgrade {revision}: {pending} account(s) are still queued for "
                "background deletion; wait for the deletion worker to finish (or supersede "
                "the deletions with a credential re-import) before downgrading"
            )
    if _INDEX in _indexes(bind):
        op.drop_index(_INDEX, table_name=_TABLE)
    if "delete_history_requested" in columns:
        op.drop_column(_TABLE, "delete_history_requested")
    if "delete_requested_at" in columns:
        op.drop_column(_TABLE, "delete_requested_at")
