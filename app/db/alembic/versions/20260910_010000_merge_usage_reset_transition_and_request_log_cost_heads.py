"""Merge usage reset transition and request-log cost index heads."""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260910_010000_merge_usage_reset_transition_and_request_log_cost_heads"
down_revision: tuple[str, str] = (
    "20260904_000000_add_usage_reset_transition_index",
    "20260910_000000_request_logs_missing_cost_index",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
