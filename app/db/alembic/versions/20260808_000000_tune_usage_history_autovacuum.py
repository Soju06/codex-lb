"""Tune usage_history autovacuum so the covering read path stays index-only.

The covering indexes from ``20260806_020000`` only pay off while the
visibility map is fresh: ``usage_history`` is append-heavy (high-frequency
usage snapshots, no updates or deletes), and PostgreSQL's default
insert-driven autovacuum trigger (20% of the table) lets the freshly
appended pages sit without an all-visible bit for a long time, turning the
"index-only" scan into one heap fetch per row — exactly the regression the
covering indexes were built to remove. This was observed on the reference
deployment and resolved by manually applying the settings below plus a
one-off ``VACUUM ANALYZE``; this revision codifies the settings.

Mirrors ``20260717_000000_optimize_dashboard_hot_path_indexes``, which set
the same parameters on the other two insert-heavy tables
(``request_logs``, ``additional_usage_history``). ``ALTER TABLE ... SET``
is idempotent, so re-applying on a deployment that already carries the
manual settings is harmless. Non-PostgreSQL backends are a no-op (SQLite
has no visibility map and serves this read from its snapshot cache).

Revision ID: 20260808_000000_tune_usage_history_autovacuum
Revises: 20260806_020000_add_usage_history_bulk_covering_indexes
Create Date: 2026-08-08 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260808_000000_tune_usage_history_autovacuum"
down_revision = "20260806_020000_add_usage_history_bulk_covering_indexes"
branch_labels = None
depends_on = None

_POSTGRES_AUTOVACUUM_SETTINGS = (
    "autovacuum_vacuum_insert_scale_factor = 0.02, "
    "autovacuum_vacuum_insert_threshold = 50000, "
    "autovacuum_analyze_scale_factor = 0.02"
)
_POSTGRES_AUTOVACUUM_RESET = (
    "autovacuum_vacuum_insert_scale_factor, autovacuum_vacuum_insert_threshold, autovacuum_analyze_scale_factor"
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE usage_history SET ({_POSTGRES_AUTOVACUUM_SETTINGS})"))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text(f"ALTER TABLE usage_history RESET ({_POSTGRES_AUTOVACUUM_RESET})"))
