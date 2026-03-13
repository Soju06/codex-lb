"""fix postgresql enum value casing from uppercase to lowercase

Revision ID: 20260310_000000_fix_postgresql_enum_value_casing
Revises: 20260309_000000_add_additional_usage_history
Create Date: 2026-03-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260310_000000_fix_postgresql_enum_value_casing"
down_revision = "20260309_000000_add_additional_usage_history"
branch_labels = None
depends_on = None

_ENUM_RENAMES: dict[str, list[tuple[str, str]]] = {
    "account_status": [
        ("ACTIVE", "active"),
        ("RATE_LIMITED", "rate_limited"),
        ("QUOTA_EXCEEDED", "quota_exceeded"),
        ("PAUSED", "paused"),
        ("DEACTIVATED", "deactivated"),
    ],
    "limit_type": [
        ("TOTAL_TOKENS", "total_tokens"),
        ("INPUT_TOKENS", "input_tokens"),
        ("OUTPUT_TOKENS", "output_tokens"),
        ("COST_USD", "cost_usd"),
    ],
    "limit_window": [
        ("DAILY", "daily"),
        ("WEEKLY", "weekly"),
        ("MONTHLY", "monthly"),
    ],
}


def _enum_value_exists(bind: sa.engine.Connection, enum_type_name: str, enum_value: str) -> bool:
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = :type_name AND e.enumlabel = :value"
        ),
        {"type_name": enum_type_name, "value": enum_value},
    )
    return result.scalar() is not None


def _rename_enum_values(
    bind: sa.engine.Connection,
    enum_type_name: str,
    renames: list[tuple[str, str]],
) -> None:
    for old_value, new_value in renames:
        if _enum_value_exists(bind, enum_type_name, old_value):
            bind.execute(sa.text(f"ALTER TYPE {enum_type_name} RENAME VALUE '{old_value}' TO '{new_value}'"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for enum_type_name, renames in _ENUM_RENAMES.items():
        _rename_enum_values(bind, enum_type_name, renames)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for enum_type_name, renames in _ENUM_RENAMES.items():
        reverse_renames = [(new_val, old_val) for old_val, new_val in renames]
        _rename_enum_values(bind, enum_type_name, reverse_renames)
