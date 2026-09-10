"""Join CPA catalog discovery and dashboard spool retention histories."""

from __future__ import annotations

revision = "20260910_020000_merge_cpa_spool_retention"
down_revision = (
    "20260910_000000_add_cpa_catalog_discovery",
    "20260910_010000_dashboard_spool_retention",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join revision tracking after both parent migrations have completed."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without changing either schema or its data."""
    pass
