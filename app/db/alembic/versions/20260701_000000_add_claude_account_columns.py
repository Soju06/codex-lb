"""add Claude account columns, provider discriminator, and api_keys.provider_scope

Revision ID: 20260701_000000_add_claude_account_columns
Revises: 20260630_020000_merge_warmup_threshold_and_main_heads
Create Date: 2026-07-01 00:00:00.000000

Adds the schema needed to pool Claude Max/Pro/Team OAuth tokens alongside
Codex OAuth tokens. See openspec/changes/add-claude-oauth-pool/specs/.

Backfill contract:
  - accounts.provider: existing rows backfilled to 'codex', then NOT NULL is enforced.
  - api_keys.provider_scope: existing rows backfilled to 'codex', then NOT NULL is enforced.
  - accounts.email: NOT NULL constraint is dropped so Claude accounts without an
    email claim can persist. The UNIQUE(email) index is intentionally NOT dropped.

Partial unique index:
  - accounts (provider='claude', claude_account_uuid) is unique. Codex rows are
    exempt from uniqueness on claude_account_uuid so legacy import paths are
    unaffected.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.engine import Connection

revision = "20260701_000000_add_claude_account_columns"
down_revision = "20260630_020000_merge_warmup_threshold_and_main_heads"
branch_labels = None
depends_on = None


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


def _check_constraints(connection: Connection, table_name: str) -> set[str]:
    """Return the set of named CHECK constraints declared on ``table_name``.

    SQLite does not expose CHECK constraint names through a clean pragma
    (the ``pragma_check_constraints`` table-valued function is gated by
    a SQLite ≥ 3.40 feature flag, which the project's pinned Python build
    does not always enable). We fall back to parsing the table's
    ``CREATE TABLE`` SQL stored in ``sqlite_master`` and extracting names
    of the form ``CONSTRAINT <name> CHECK (...)``. Postgres exposes
    ``information_schema.table_constraints`` directly so the cross-dialect
    branch below is cheap.
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
        import re

        # ``CREATE TABLE foo (..., CONSTRAINT ck_foo CHECK (...))`` — match
        # each named CHECK constraint. Bare ``CHECK (...)`` clauses without
        # a ``CONSTRAINT <name>`` wrapper are anonymous and can't be looked
        # up by name; they don't matter for this migration.
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
    accounts_columns = _columns(bind, "accounts")
    api_keys_columns = _columns(bind, "api_keys")
    request_logs_columns = _columns(bind, "request_logs")
    accounts_check_constraints = _check_constraints(bind, "accounts")

    # 1. Add the new accounts columns as NULLABLE first so the backfill can run
    #    without violating a NOT NULL constraint. The CHECK constraint on
    #    provider is added at the same time so the partial unique index can
    #    reference 'provider = 'claude'' safely.
    if accounts_columns:
        with op.batch_alter_table("accounts") as batch_op:
            if "provider" not in accounts_columns:
                batch_op.add_column(sa.Column("provider", sa.Text(), nullable=True))
            if "claude_account_uuid" not in accounts_columns:
                batch_op.add_column(sa.Column("claude_account_uuid", sa.Text(), nullable=True))
            if "claude_refresh_token_encrypted" not in accounts_columns:
                batch_op.add_column(sa.Column("claude_refresh_token_encrypted", sa.LargeBinary(), nullable=True))
            if "claude_access_token_encrypted" not in accounts_columns:
                batch_op.add_column(sa.Column("claude_access_token_encrypted", sa.LargeBinary(), nullable=True))
            if "claude_access_token_expires_at" not in accounts_columns:
                batch_op.add_column(
                    sa.Column("claude_access_token_expires_at", sa.DateTime(timezone=True), nullable=True)
                )
            if "claude_scopes" not in accounts_columns:
                batch_op.add_column(sa.Column("claude_scopes", sa.Text(), nullable=True))
            if "claude_user_email" not in accounts_columns:
                batch_op.add_column(sa.Column("claude_user_email", sa.Text(), nullable=True))
            if "claude_user_organization_uuid" not in accounts_columns:
                batch_op.add_column(sa.Column("claude_user_organization_uuid", sa.Text(), nullable=True))
            if "rate_limit_requests_remaining" not in accounts_columns:
                batch_op.add_column(sa.Column("rate_limit_requests_remaining", sa.Integer(), nullable=True))
            if "rate_limit_requests_reset_at" not in accounts_columns:
                batch_op.add_column(
                    sa.Column("rate_limit_requests_reset_at", sa.DateTime(timezone=True), nullable=True)
                )
            if "rate_limit_input_tokens_remaining" not in accounts_columns:
                batch_op.add_column(sa.Column("rate_limit_input_tokens_remaining", sa.Integer(), nullable=True))
            if "rate_limit_input_tokens_reset_at" not in accounts_columns:
                batch_op.add_column(
                    sa.Column("rate_limit_input_tokens_reset_at", sa.DateTime(timezone=True), nullable=True)
                )
            if "rate_limit_output_tokens_remaining" not in accounts_columns:
                batch_op.add_column(sa.Column("rate_limit_output_tokens_remaining", sa.Integer(), nullable=True))
            if "rate_limit_output_tokens_reset_at" not in accounts_columns:
                batch_op.add_column(
                    sa.Column("rate_limit_output_tokens_reset_at", sa.DateTime(timezone=True), nullable=True)
                )
            if "rate_limit_status" not in accounts_columns:
                batch_op.add_column(sa.Column("rate_limit_status", sa.Text(), nullable=True))
            # Drop NOT NULL on email so Claude accounts without an email claim
            # can persist. The UNIQUE(email) index is intentionally preserved.
            batch_op.alter_column("email", existing_type=sa.Text(), nullable=True)
            # The ``ck_accounts_provider`` constraint is also defined on the
            # ORM model (``app/db/models.py::Account.__table_args__``); when the
            # migration runs against a freshly-bootstrapped schema (i.e. one
            # created via ``Base.metadata.create_all`` from a ``bootstrap_legacy``
            # migration run) the constraint already exists and re-emitting
            # ``ALTER TABLE … ADD CONSTRAINT`` would fail with
            # ``DuplicateObject``. Guard on the inspector's view of the
            # current constraints so the migration is idempotent across both
            # bootstrap-legacy and bare ``upgrade head`` flows.
            if "ck_accounts_provider" not in accounts_check_constraints:
                batch_op.create_check_constraint("ck_accounts_provider", "provider IN ('codex', 'claude')")

    # 2. Backfill provider='codex' for existing accounts.
    if accounts_columns and "provider" in _columns(bind, "accounts"):
        bind.execute(sa.text("UPDATE accounts SET provider = 'codex' WHERE provider IS NULL"))

    # 3. Enforce NOT NULL on accounts.provider with a server_default so future
    #    inserts without an explicit value still satisfy the constraint.
    if accounts_columns and "provider" in _columns(bind, "accounts"):
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.alter_column(
                "provider",
                existing_type=sa.Text(),
                nullable=False,
                server_default=sa.text("'codex'"),
            )

    # 4. Partial unique index on (provider='claude', claude_account_uuid).
    if accounts_columns and "uq_accounts_claude_uuid" not in _indexes(bind, "accounts"):
        op.create_index(
            "uq_accounts_claude_uuid",
            "accounts",
            ["claude_account_uuid"],
            unique=True,
            sqlite_where=sa.text("provider = 'claude'"),
            postgresql_where=sa.text("provider = 'claude'"),
        )

    # 5. api_keys.provider_scope: add nullable, backfill to 'codex', enforce NOT NULL.
    if api_keys_columns:
        with op.batch_alter_table("api_keys") as batch_op:
            if "provider_scope" not in api_keys_columns:
                batch_op.add_column(sa.Column("provider_scope", sa.Text(), nullable=True))

    if api_keys_columns and "provider_scope" in _columns(bind, "api_keys"):
        bind.execute(sa.text("UPDATE api_keys SET provider_scope = 'codex' WHERE provider_scope IS NULL"))

    if api_keys_columns and "provider_scope" in _columns(bind, "api_keys"):
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.alter_column(
                "provider_scope",
                existing_type=sa.Text(),
                nullable=False,
                server_default=sa.text("'codex'"),
            )

    # 6. request_logs.provider: nullable column, no backfill needed (existing rows
    #    are pre-Claude and may legitimately have NULL).
    if request_logs_columns and "provider" not in request_logs_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.add_column(sa.Column("provider", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()

    # Reverse order of upgrade: drop request_logs.provider first.
    request_logs_columns = _columns(bind, "request_logs")
    if request_logs_columns and "provider" in request_logs_columns:
        with op.batch_alter_table("request_logs") as batch_op:
            batch_op.drop_column("provider")

    # Drop api_keys.provider_scope.
    api_keys_columns = _columns(bind, "api_keys")
    if api_keys_columns and "provider_scope" in api_keys_columns:
        with op.batch_alter_table("api_keys") as batch_op:
            batch_op.drop_column("provider_scope")

    # Drop the partial unique index, then the CHECK constraint, then restore the
    # NOT NULL on email, then drop the new accounts columns in reverse order.
    accounts_columns = _columns(bind, "accounts")
    accounts_indexes = _indexes(bind, "accounts")
    if accounts_columns:
        if "uq_accounts_claude_uuid" in accounts_indexes:
            op.drop_index("uq_accounts_claude_uuid", table_name="accounts")

        with op.batch_alter_table("accounts") as batch_op:
            batch_op.drop_constraint("ck_accounts_provider", type_="check")
            # Restore NOT NULL on email. This will fail if any pre-existing
            # row was inserted with a NULL email by an in-flight code path;
            # the migration is intentionally strict to surface that case.
            batch_op.alter_column("email", existing_type=sa.Text(), nullable=False)
            batch_op.drop_column("rate_limit_status")
            batch_op.drop_column("rate_limit_output_tokens_reset_at")
            batch_op.drop_column("rate_limit_output_tokens_remaining")
            batch_op.drop_column("rate_limit_input_tokens_reset_at")
            batch_op.drop_column("rate_limit_input_tokens_remaining")
            batch_op.drop_column("rate_limit_requests_reset_at")
            batch_op.drop_column("rate_limit_requests_remaining")
            batch_op.drop_column("claude_user_organization_uuid")
            batch_op.drop_column("claude_user_email")
            batch_op.drop_column("claude_scopes")
            batch_op.drop_column("claude_access_token_expires_at")
            batch_op.drop_column("claude_access_token_encrypted")
            batch_op.drop_column("claude_refresh_token_encrypted")
            batch_op.drop_column("claude_account_uuid")
            batch_op.drop_column("provider")
