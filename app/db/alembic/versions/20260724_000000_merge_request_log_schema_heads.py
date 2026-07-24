"""merge the deployed retry-circuit and request-log schema heads

Revision ID: 20260724_000000_merge_request_log_schema_heads
Revises:
- 20260717_000001_merge_retry_circuits_and_dashboard_indexes
- 20260722_000000_backfill_request_log_useragent_families
Create Date: 2026-07-24 00:00:00.000000

The deployed SQLite database was previously stamped at the retry-circuit
merge revision while the request-log conversation-id branch was not applied.
Keeping both parents here lets Alembic apply the missing 20260720 and 20260722
revisions before converging on one head without rewriting migration history.
"""

from __future__ import annotations

revision = "20260724_000000_merge_request_log_schema_heads"
down_revision = (
    "20260717_000001_merge_retry_circuits_and_dashboard_indexes",
    "20260722_000000_backfill_request_log_useragent_families",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
