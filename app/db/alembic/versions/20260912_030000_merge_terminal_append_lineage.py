"""Join the preserved terminal-append parent with the recovery lineage.

The terminal-append revision is already deployed with its original parent
``20260911_010000_merge_pin_index_and_affinity_heads``. Keep that historical
link unchanged and converge the additional HTTP-bridge/affinity merge with a
metadata-only revision instead.
"""

from __future__ import annotations

revision = "20260912_030000_merge_terminal_append_lineage"
down_revision = (
    "20260912_020000_rehome_recovery_repair_ownership",
    "20260911_015000_merge_http_bridge_and_affinity_heads",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge both histories without changing schema or data."""


def downgrade() -> None:
    """Restore both parent stamps without changing schema or data."""
