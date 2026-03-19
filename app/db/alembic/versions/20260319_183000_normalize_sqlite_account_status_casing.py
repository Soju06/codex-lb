"""normalize sqlite account status casing

Revision ID: 20260319_183000_normalize_sqlite_account_status_casing
Revises: 20260319_111500_merge_prompt_cache_ttl_and_upstream_transport_heads
Create Date: 2026-03-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260319_183000_normalize_sqlite_account_status_casing"
down_revision = "20260319_111500_merge_prompt_cache_ttl_and_upstream_transport_heads"
branch_labels = None
depends_on = None

_ACCOUNT_STATUS_RENAMES: tuple[tuple[str, str], ...] = (
    ("ACTIVE", "active"),
    ("RATE_LIMITED", "rate_limited"),
    ("QUOTA_EXCEEDED", "quota_exceeded"),
    ("PAUSED", "paused"),
    ("DEACTIVATED", "deactivated"),
)


def _apply_status_renames(bind: sa.engine.Connection, renames: tuple[tuple[str, str], ...]) -> None:
    for old_value, new_value in renames:
        bind.execute(
            sa.text("UPDATE accounts SET status = :new_value WHERE status = :old_value"),
            {"old_value": old_value, "new_value": new_value},
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    _apply_status_renames(bind, _ACCOUNT_STATUS_RENAMES)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return
    reverse_renames = tuple((new_value, old_value) for old_value, new_value in _ACCOUNT_STATUS_RENAMES)
    _apply_status_renames(bind, reverse_renames)
