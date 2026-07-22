"""Normalize request-log user-agent groups.

Revision ID: 20260720_000000_normalize_request_log_useragent_groups
Revises: 20260717_000000_optimize_dashboard_hot_path_indexes
Create Date: 2026-07-20 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260720_000000_normalize_request_log_useragent_groups"
down_revision = "20260717_000000_optimize_dashboard_hot_path_indexes"
branch_labels = None
depends_on = None


# SQLite and PostgreSQL do not expose the same Unicode whitespace behavior as
# Python's str.strip(). Keep the database trim set aligned with the parser.
_PYTHON_STRIP_CODEPOINTS = (
    9,
    10,
    11,
    12,
    13,
    28,
    29,
    30,
    31,
    32,
    133,
    160,
    5760,
    *range(8192, 8203),
    8232,
    8233,
    8239,
    8287,
    12288,
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        whitespace = " || ".join(f"chr({codepoint})" for codepoint in _PYTHON_STRIP_CODEPOINTS)
        useragent = f"btrim(useragent, {whitespace})"
        slash_position = f"strpos({useragent}, '/')"
        group = f"btrim(substr({useragent}, 1, {slash_position} - 1), {whitespace})"
    else:
        whitespace = f"char({', '.join(str(codepoint) for codepoint in _PYTHON_STRIP_CODEPOINTS)})"
        useragent = f"trim(useragent, {whitespace})"
        slash_position = f"instr({useragent}, '/')"
        group = f"trim(substr({useragent}, 1, {slash_position} - 1), {whitespace})"

    op.execute(
        sa.text(
            f"""
            UPDATE request_logs
            SET useragent_group = CASE
                WHEN useragent IS NULL OR {useragent} = '' THEN NULL
                WHEN {slash_position} > 0 THEN NULLIF({group}, '')
                ELSE {useragent}
            END
            """
        )
    )


def downgrade() -> None:
    pass
