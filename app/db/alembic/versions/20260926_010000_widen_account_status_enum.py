"""widen accounts.status to every AccountStatus value (MySQL port branch)

The refresh path writes ``reauth_required``. ``SqlEnum(AccountStatus)`` declares
it, SQLite stores the column as a VARCHAR and PostgreSQL widened its native type,
but the MySQL/MariaDB ``accounts.status`` ENUM was created with five values and
never gained the sixth, so every reauth write failed with
``DataError 1265 (Data truncated for column 'status')``.

The value is *appended* rather than inserted in model order: appending keeps the
stored index of every existing row unchanged.

Revision ID: 20260926_010000_widen_account_status_enum
Revises: 20260922_000000_widen_epoch_second_columns
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.dialect_sql import is_mysql

# revision identifiers, used by Alembic.
revision = "20260926_010000_widen_account_status_enum"
down_revision = "20260922_000000_widen_epoch_second_columns"
branch_labels = None
depends_on = None

#: The enum as the schema has it, plus the value the model added.
_WIDENED_STATUSES = (
    "active",
    "rate_limited",
    "quota_exceeded",
    "paused",
    "deactivated",
    "reauth_required",
)

_REAUTH_STATUS = "reauth_required"


def _enum_sql(values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"ENUM({rendered})"


def upgrade() -> None:
    bind = op.get_bind()
    if is_mysql(bind):
        op.execute(sa.text(f"ALTER TABLE accounts MODIFY status {_enum_sql(_WIDENED_STATUSES)} NOT NULL"))


def downgrade() -> None:
    bind = op.get_bind()
    if not is_mysql(bind):
        return
    # A narrower ENUM cannot hold the state that goes away: park those accounts.
    op.execute(sa.text(f"UPDATE accounts SET status = 'paused' WHERE status = '{_REAUTH_STATUS}'"))
    remaining = tuple(value for value in _WIDENED_STATUSES if value != _REAUTH_STATUS)
    op.execute(sa.text(f"ALTER TABLE accounts MODIFY status {_enum_sql(remaining)} NOT NULL"))
