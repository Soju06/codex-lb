"""Repair overflow schema skipped by databases stamped at the old usage-cap head.

Revision ID: 20260909_090000_repair_reparented_overflow_schema
Revises: 20260907_000000_add_account_usage_caps, 20260909_070000_automation_run_claim_budget
Create Date: 2026-09-09

The usage-cap revision was released on this feature branch before it was
reparented behind the overflow and transport revisions. Alembic cannot replay
new ancestors for a database already stamped at that revision, so run their
idempotent upgrades once from a new forward-only descendant.
"""

from __future__ import annotations

import importlib
from types import ModuleType

revision = "20260909_090000_repair_reparented_overflow_schema"
down_revision = (
    "20260907_000000_add_account_usage_caps",
    "20260909_070000_automation_run_claim_budget",
)
branch_labels = None
depends_on = None


def _migration(name: str) -> ModuleType:
    return importlib.import_module(f"app.db.alembic.versions.{name}")


def upgrade() -> None:
    _migration("20260908_000000_add_subscription_overflow").upgrade()
    _migration("20260908_000000_replace_upstream_stream_transport_default_sentinel").upgrade()


def downgrade() -> None:
    # The canonical revisions own these objects and remain ancestors of the
    # usage-cap revision. A repair downgrade must not remove their schema.
    pass
