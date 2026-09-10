"""bootstrap the shared HTTP bridge migration ownership registry

Some databases were stamped through the persisted recovery repair after the
historical operation index already existed.  In that shape the conditional
parent repair did not create the ownership-marker table required by ORM
metadata, so the application failed its post-migration drift guard.  Keep
this forward-only repair idempotent and preserve all parent objects.
"""

from __future__ import annotations

from alembic import op

from app.db.alembic.http_bridge_migration_ownership import ensure_ownership_table

revision = "20260904_000000_repair_http_bridge_ownership_registry"
down_revision = "20260901_000000_repair_persisted_schema_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    ensure_ownership_table(op.get_bind())


def downgrade() -> None:
    """Keep the shared ownership registry and parent objects intact."""
