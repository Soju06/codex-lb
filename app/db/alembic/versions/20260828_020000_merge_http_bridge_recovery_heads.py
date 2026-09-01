"""reconcile the persisted HTTP bridge recovery migration stamp

Revision ID: 20260828_020000_merge_http_bridge_recovery_heads
Revises: 20260815_000000_add_http_bridge_recent_unknown_index
Create Date: 2026-08-28

This revision was applied by an earlier recovery build as a metadata-only
merge of the HTTP bridge recovery heads.  Keep the revision in the graph so
those databases can start on a newer build without being mistaken for an
unknown, ahead-of-head schema.  The schema changes represented by the parent
chain are already present; this migration intentionally performs no DDL.
"""

from __future__ import annotations

revision = "20260828_020000_merge_http_bridge_recovery_heads"
down_revision = "20260815_000000_add_http_bridge_recent_unknown_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Record the compatibility merge without changing the schema."""


def downgrade() -> None:
    """Return to the prior recovery head without changing the schema."""
