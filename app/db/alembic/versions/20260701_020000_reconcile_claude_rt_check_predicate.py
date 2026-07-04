"""reconcile ck_accounts_claude_rt_required against bootstrap-created schemas

Revision ID: 20260701_020000_reconcile_claude_rt_check_predicate
Revises: 20260701_010000_enforce_claude_rt_and_codex_email_invariants
Create Date: 2026-07-01 02:00:00.000000

The prior revision (``20260701_010000``) added
``ck_accounts_claude_rt_required`` without an idempotency guard against
schemas bootstrapped via ``Base.metadata.create_all``. When the ORM
model declared the constraint first, the migration's
``batch_op.create_check_constraint`` would fail with ``DuplicateObject``.

This forward-only revision drops the constraint if it already exists in
ANY shape and recreates it with the canonical predicate
``((provider = 'claude') AND (claude_refresh_token_encrypted IS NOT NULL))
OR ((provider = 'codex') AND (claude_refresh_token_encrypted IS NULL))``.
Re-running the migration is a no-op because the recreated constraint has
the same shape every time. The prior migration's guard for
``ck_accounts_provider`` is intentionally NOT repeated here; the prior
revision's pattern was already correct for that constraint.
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision = "20260701_020000_reconcile_claude_rt_check_predicate"
down_revision = "20260701_010000_enforce_claude_rt_and_codex_email_invariants"
branch_labels = None
depends_on = None


_CLAUDE_RT_CHECK_NAME = "ck_accounts_claude_rt_required"

# Canonical predicate for the constraint; matches both the model
# (``app/db/models.py::Account.__table_args__``) and the prior migration.
_CLAUDE_RT_CHECK_PREDICATE = (
    "((provider = 'claude') AND (claude_refresh_token_encrypted IS NOT NULL)) "
    "OR ((provider = 'codex') AND (claude_refresh_token_encrypted IS NULL))"
)


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _check_constraints(connection: Connection, table_name: str) -> set[str]:
    """Return the set of named CHECK constraints on ``table_name``.

    Mirrors the helper from ``20260701_000000_add_claude_account_columns``.
    SQLite does not expose CHECK constraint names via a clean pragma on
    the pinned Python build, so we parse the table's ``CREATE TABLE``
    SQL stored in ``sqlite_master``; Postgres uses
    ``information_schema.table_constraints``.
    """
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    dialect = connection.dialect.name
    if dialect == "sqlite":
        result = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"),
            {"name": table_name},
        ).scalar_one_or_none()
        if not result:
            return set()
        names: set[str] = set()
        for match in re.finditer(
            r"CONSTRAINT\s+([A-Za-z_][A-Za-z0-9_]*)\s+CHECK\s*\(",
            result,
            re.IGNORECASE,
        ):
            names.add(match.group(1))
        return names
    rows = connection.execute(
        text(
            "SELECT constraint_name FROM information_schema.table_constraints "
            "WHERE table_name = :table_name AND constraint_type = 'CHECK'"
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows}


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "accounts"):
        return

    accounts_check_constraints = _check_constraints(bind, "accounts")
    if _CLAUDE_RT_CHECK_NAME not in accounts_check_constraints:
        # Fresh deployment or a schema where the constraint was never
        # added (e.g. ``ck_accounts_provider` was created but not the
        # ``claude_rt_required`` companion). Add it.
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.create_check_constraint(_CLAUDE_RT_CHECK_NAME, _CLAUDE_RT_CHECK_PREDICATE)
        return

    # Constraint exists (possibly with the pre-PR-#2 model shape
    # ``provider != 'claude'``). Drop and recreate so the canonical
    # predicate is enforced regardless of how the constraint was first
    # produced. SQLite ``batch_alter_table`` requires the drop and
    # create to live in distinct ``batch_alter_table`` blocks — mixing
    # them in one block is rejected on SQLite.
    with op.batch_alter_table("accounts") as batch_op:
        batch_op.drop_constraint(_CLAUDE_RT_CHECK_NAME, type_="check")

    with op.batch_alter_table("accounts") as batch_op:
        batch_op.create_check_constraint(_CLAUDE_RT_CHECK_NAME, _CLAUDE_RT_CHECK_PREDICATE)


def downgrade() -> None:
    # Intentionally a no-op. The ``ck_accounts_claude_rt_required``
    # constraint is dropped by the next revision's downgrade
    # (``20260701_010000_enforce_claude_rt_and_codex_email_invariants``)
    # which runs immediately after this one in the downgrade chain and
    # owns the constraint's lifecycle. Re-dropping it here would race with
    # the older migration's ``batch_op.drop_constraint`` call and surface
    # ``ValueError: No such constraint`` on the second pass of an
    # upgrade → downgrade → upgrade round trip.
    return
