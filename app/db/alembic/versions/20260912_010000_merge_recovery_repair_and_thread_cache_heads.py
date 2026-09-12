"""Merge the HTTP bridge recovery repair with the latest upstream heads.

The recovery repair revision predates the upstream merge that converged the
thread-cache and bridge-retirement branches. Keep that revision's identifier
stable for databases that already applied it, then converge both histories in
this metadata-only merge.
"""

from __future__ import annotations

revision = "20260912_010000_merge_recovery_repair_and_thread_cache_heads"
down_revision = (
    "20260911_070000_repair_http_bridge_recovery_columns",
    "20260912_000000_merge_thread_cache_and_bridge_retirement_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge both version histories without changing schemas or rows."""


def downgrade() -> None:
    """Restore both parent stamps without changing schemas or rows."""
