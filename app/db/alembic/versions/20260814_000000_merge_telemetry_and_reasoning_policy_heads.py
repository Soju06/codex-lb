"""merge telemetry and API-key reasoning policy heads.

Current main added anonymous telemetry on top of the existing merged
recovery/hourly-rollup head, while PR #1642 adds the API-key reasoning-policy
column from the hourly-rollup side branch. Keep both additive revisions intact
and restore a single Alembic head with a no-op merge revision.
"""

from __future__ import annotations

revision = "20260814_000000_merge_telemetry_and_reasoning_policy_heads"
down_revision = (
    "20260806_000000_add_anonymous_telemetry",
    "20260806_030000_add_api_key_allowed_reasoning_efforts",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
