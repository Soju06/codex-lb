"""Add nullable HTTP bridge dispatch generation without rewriting existing data.

This upgrade is not rolling-safe. The Helm migration Job is a ``pre-upgrade``
hook, so it runs before the StatefulSet replaces and drains old replicas. A
pre-generation worker can still update an operation without a generation
predicate after a newer dispatch advances the row, overwriting its state or
response identity. Every old replica must therefore be stopped before this
migration runs. ``run_upgrade`` warns before the DDL; the exact supported Helm
sequence lives in ``docs/deployment/kubernetes.md``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261002_000000_add_http_bridge_dispatch_generation"
down_revision = "20260918_000000_merge_scim_and_overflow_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No default/backfill: old in-flight rows must not acquire dispatch authority.
    columns = sa.inspect(op.get_bind()).get_columns("http_bridge_operations")
    if not any(column["name"] == "dispatch_generation" for column in columns):
        op.add_column("http_bridge_operations", sa.Column("dispatch_generation", sa.Integer(), nullable=True))


def downgrade() -> None:
    # SQLite's native DROP COLUMN avoids rebuilding the parent and cascading
    # deletion of historical event rows/chunks. Requires a quiesced runtime.
    columns = sa.inspect(op.get_bind()).get_columns("http_bridge_operations")
    if any(column["name"] == "dispatch_generation" for column in columns):
        op.drop_column("http_bridge_operations", "dispatch_generation")
