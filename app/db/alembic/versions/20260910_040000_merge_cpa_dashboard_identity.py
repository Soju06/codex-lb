"""Join published CPA and dashboard identity migration histories."""

from __future__ import annotations

revision = "20260910_040000_merge_cpa_dashboard_identity"
down_revision = (
    "20260910_030000_merge_cpa_guest_heads",
    "20260909_030000_add_audit_actor_columns",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Join stamps after both unchanged parent histories have completed."""
    pass


def downgrade() -> None:
    """Restore both parent stamps without changing either schema or its data."""
    pass
