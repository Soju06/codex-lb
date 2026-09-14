"""Join the recovery lineage before retiring legacy dashboard credentials.

The credential-drop migration was authored from the upstream thread-cache
lineage while the HTTP-bridge recovery work has its own deployed merge.  This
metadata-only merge keeps both histories in the upgrade path and leaves the
credential-drop revision with one direct parent for relative-target handling.
"""

from __future__ import annotations

revision = "20260912_010001_merge_credential_drop_lineage"
down_revision = (
    "20260912_010000_merge_recovery_repair_and_thread_cache_heads",
    "20260912_000000_merge_thread_cache_and_bridge_retirement_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge both version histories without changing schema or data."""


def downgrade() -> None:
    """Restore both parent stamps without changing schema or data."""
