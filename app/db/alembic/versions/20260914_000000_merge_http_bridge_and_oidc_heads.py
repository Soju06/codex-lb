"""Converge the HTTP-bridge recovery and OIDC migration lineages.

The HTTP-bridge changes and the upstream OIDC provider flow were developed on
separate branches.  Both histories are retained; this metadata-only revision
gives Alembic one deterministic head after the rebase.
"""

from __future__ import annotations

revision = "20260914_000000_merge_http_bridge_and_oidc_heads"
down_revision = (
    "20260912_030000_merge_terminal_append_lineage",
    "20260913_010000_add_oidc_provider_flow",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Converge both version histories without changing schema or data."""


def downgrade() -> None:
    """Restore both parent stamps without changing schema or data."""
