"""backfill request log useragent families

Revision ID: 20260722_000000_backfill_request_log_useragent_families
Revises: 20260720_000000_add_request_log_conversation_id
Create Date: 2026-07-22 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260722_000000_backfill_request_log_useragent_families"
down_revision = "20260720_000000_add_request_log_conversation_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect_name = op.get_bind().dialect.name
    if dialect_name == "postgresql":
        op.execute(
            sa.text(
                "UPDATE request_logs "
                "SET useragent_group = substring(useragent from 1 for position('/' in useragent) - 1) "
                "WHERE useragent IS NOT NULL AND position('/' in useragent) > 0"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE request_logs "
                "SET useragent_group = substr(useragent, 1, instr(useragent, '/') - 1) "
                "WHERE useragent IS NOT NULL AND instr(useragent, '/') > 0"
            )
        )


def downgrade() -> None:
    pass
