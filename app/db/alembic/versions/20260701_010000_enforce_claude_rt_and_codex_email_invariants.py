"""enforce claude refresh-token and Codex email invariants

Revision ID: 20260701_010000_enforce_claude_rt_and_codex_email_invariants
Revises: 20260701_000000_add_claude_account_columns
Create Date: 2026-07-01 01:00:00.000000

Adds the invariants required by the OpenSpec change ``add-claude-oauth-pool``:

  * ``ck_accounts_claude_rt_required`` — every row with ``provider='claude'``
    MUST have a non-NULL ``claude_refresh_token_encrypted`` ciphertext column.
    Symmetrically, Codex rows must NOT carry a non-NULL refresh token.

  * ``uq_accounts_codex_email`` — partial UNIQUE index on
    ``accounts(email) WHERE provider='codex'``. The prior migration
    (``20260218_000100_add_import_without_overwrite_and_drop_accounts_email_unique``)
    dropped the legacy UNIQUE(email) constraint to support email-less imports;
    this revision restores the Codex-only invariant the spec requires.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260701_010000_enforce_claude_rt_and_codex_email_invariants"
down_revision = "20260701_000000_add_claude_account_columns"
branch_labels = None
depends_on = None


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _columns(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _indexes(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "accounts"):
        return

    accounts_columns = _columns(bind, "accounts")
    accounts_indexes = _indexes(bind, "accounts")

    # 1. CHECK constraint: every Claude row MUST carry an encrypted refresh
    #    token. Codex rows with a non-NULL refresh token are rejected
    #    symmetrically. SQLite needs batch_alter_table to add a CHECK; we
    #    use the same shape on both dialects so the SQL is identical
    #    regardless of the underlying engine.
    #
    #    The predicate enumerates the two valid pairs explicitly instead of
    #    using `provider != 'claude'`. Combined with the NOT NULL constraint
    #    added in the prior revision, this means a row whose provider
    #    slipped through as NULL (an unrecoverable data-integrity bug) is
    #    rejected at the database level rather than silently passing the
    #    constraint via NULL-comparison semantics.
    if "claude_refresh_token_encrypted" in accounts_columns:
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.create_check_constraint(
                "ck_accounts_claude_rt_required",
                "((provider = 'claude') AND (claude_refresh_token_encrypted IS NOT NULL)) "
                "OR ((provider = 'codex') AND (claude_refresh_token_encrypted IS NULL))",
            )

    # 2. Partial UNIQUE index on accounts(email) WHERE provider='codex'.
    #    Restores the Codex-only email uniqueness invariant required by the
    #    account-routing spec.
    if "email" in accounts_columns and "uq_accounts_codex_email" not in accounts_indexes:
        op.create_index(
            "uq_accounts_codex_email",
            "accounts",
            ["email"],
            unique=True,
            sqlite_where=sa.text("provider = 'codex'"),
            postgresql_where=sa.text("provider = 'codex'"),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "accounts"):
        return

    accounts_indexes = _indexes(bind, "accounts")

    if "uq_accounts_codex_email" in accounts_indexes:
        op.drop_index("uq_accounts_codex_email", table_name="accounts")

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint("ck_accounts_claude_rt_required", type_="check")
