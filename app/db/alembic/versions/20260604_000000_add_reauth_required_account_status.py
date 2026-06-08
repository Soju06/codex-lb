"""add reauth_required account status

Revision ID: 20260604_000000_add_reauth_required_account_status
Revises: 20260602_080000_merge_upstream_proxy_and_quota_planner_heads
Create Date: 2026-06-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260604_000000_add_reauth_required_account_status"
down_revision = "20260602_080000_merge_upstream_proxy_and_quota_planner_heads"
branch_labels = None
depends_on = None

_ACCOUNT_STATUS_VALUES = (
    "active",
    "rate_limited",
    "quota_exceeded",
    "paused",
    "reauth_required",
    "deactivated",
)

_LEGACY_ACCOUNT_STATUS_VALUES = (
    "active",
    "rate_limited",
    "quota_exceeded",
    "paused",
    "deactivated",
)

_REAUTH_REQUIRED_DEACTIVATION_REASONS = (
    "Refresh token expired - re-login required",
    "Refresh token was reused - re-login required",
    "Refresh token was revoked - re-login required",
    "Refresh token grant invalid - re-login required",
    "Authentication token invalidated - re-login required",
    "Authentication token expired - re-login required",
    "ChatGPT session ended - re-login required",
    "Authentication failed after token refresh - re-login required",
)

_REAUTH_REQUIRED_USAGE_REASON_PATTERNS = (
    "%re-login required%",
    "%try signing in again%",
    "%token%invalidated%",
    "%token%expired%",
    "%refresh token%expired%",
    "%refresh token%reused%",
    "%refresh token%revoked%",
    "%invalid_grant%",
    "%session%ended%",
    "%session%expired%",
    "%authentication failed after token refresh%",
)


def _account_status_enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values,
        name="account_status",
        validate_strings=True,
        create_type=False,
    )


def _enum_value_exists(enum_type_name: str, enum_value: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = :type_name AND e.enumlabel = :value"
        ),
        {"type_name": enum_type_name, "value": enum_value},
    )
    return result.scalar() is not None


def _replace_postgresql_account_status_enum(values: tuple[str, ...]) -> None:
    enum_values_sql = ", ".join(f"'{value}'" for value in values)
    op.execute(sa.text("ALTER TYPE account_status RENAME TO account_status_old"))
    op.execute(sa.text(f"CREATE TYPE account_status AS ENUM ({enum_values_sql})"))
    op.execute(
        sa.text("ALTER TABLE accounts ALTER COLUMN status TYPE account_status USING status::text::account_status")
    )
    op.execute(sa.text("DROP TYPE account_status_old"))


def _backfill_reauth_required_status() -> None:
    usage_reason_predicates = " OR ".join(
        f"lower(deactivation_reason) LIKE :usage_reason_pattern_{index}"
        for index, _pattern in enumerate(_REAUTH_REQUIRED_USAGE_REASON_PATTERNS)
    )
    statement = sa.text(
        "UPDATE accounts "
        "SET status = 'reauth_required' "
        "WHERE status = 'deactivated' "
        "AND ("
        "deactivation_reason IN :reasons "
        "OR ("
        "lower(deactivation_reason) LIKE :usage_reason_prefix "
        f"AND ({usage_reason_predicates})"
        ")"
        ")"
    ).bindparams(sa.bindparam("reasons", expanding=True))
    params = {
        "reasons": _REAUTH_REQUIRED_DEACTIVATION_REASONS,
        "usage_reason_prefix": "usage api error: http %",
    }
    params.update(
        {
            f"usage_reason_pattern_{index}": pattern
            for index, pattern in enumerate(_REAUTH_REQUIRED_USAGE_REASON_PATTERNS)
        }
    )
    op.get_bind().execute(statement, params)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=_account_status_enum(_LEGACY_ACCOUNT_STATUS_VALUES),
                type_=_account_status_enum(_ACCOUNT_STATUS_VALUES),
                existing_nullable=False,
            )
        _backfill_reauth_required_status()
        return
    if bind.dialect.name != "postgresql":
        return
    if not _enum_value_exists("account_status", "reauth_required"):
        with op.get_context().autocommit_block():
            op.execute(sa.text("ALTER TYPE account_status ADD VALUE 'reauth_required' BEFORE 'deactivated'"))
    _backfill_reauth_required_status()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(sa.text("UPDATE accounts SET status = 'deactivated' WHERE status = 'reauth_required'"))
        with op.batch_alter_table("accounts") as batch_op:
            batch_op.alter_column(
                "status",
                existing_type=_account_status_enum(_ACCOUNT_STATUS_VALUES),
                type_=_account_status_enum(_LEGACY_ACCOUNT_STATUS_VALUES),
                existing_nullable=False,
            )
        return
    if bind.dialect.name != "postgresql":
        return
    op.execute(sa.text("UPDATE accounts SET status = 'deactivated' WHERE status = 'reauth_required'"))
    if _enum_value_exists("account_status", "reauth_required"):
        _replace_postgresql_account_status_enum(_LEGACY_ACCOUNT_STATUS_VALUES)
