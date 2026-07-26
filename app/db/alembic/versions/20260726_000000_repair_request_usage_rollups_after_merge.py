"""repair request-usage rollups for databases stamped at the old merge head

Revision ID: 20260726_000000_repair_request_usage_rollups_after_merge
Revises: 20260724_000000_merge_request_log_schema_heads
Create Date: 2026-07-26

Some deployed databases were stamped at the request-log merge revision before
the request-usage rollup child was connected to that merge. Changing the
parent tuple cannot make Alembic replay an already-applied revision, so those
databases need a forward-only repair step. The canonical rollup migration is
idempotent and safely creates any missing tables or watermark column here.
"""

from __future__ import annotations

import importlib
from types import ModuleType

revision = "20260726_000000_repair_request_usage_rollups_after_merge"
down_revision = "20260724_000000_merge_request_log_schema_heads"
branch_labels = None
depends_on = None


def _rollup_migration() -> ModuleType:
    return importlib.import_module("app.db.alembic.versions.20260724_000000_add_request_usage_time_rollups")


def upgrade() -> None:
    _rollup_migration().upgrade()


def downgrade() -> None:
    # This revision repairs databases that were already stamped at the merge
    # head. It must never remove objects owned by the canonical rollup
    # revision, which remains an ancestor of that merge head on fresh installs.
    pass
