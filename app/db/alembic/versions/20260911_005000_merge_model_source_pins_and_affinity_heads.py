"""Merge the model-source-pin index and affinity/invite histories.

Two migrations landed on main concurrently and left the tree with two heads
(`20260910_220000_merge_affinity_invite_heads` and
`20260911_000000_model_source_pins_kind_expires_index`), which fails every
`alembic upgrade head` and the single-head contract in `tests/unit/test_db_migrate.py`.

This is a pure merge point: no schema change, no data change. It is kept in a
separate file from the migration that follows it so that if a dedicated merge
lands on main first, only this file conflicts and the feature migration rebases
cleanly.
"""

from __future__ import annotations

revision = "20260911_005000_merge_model_source_pins_and_affinity_heads"
down_revision = (
    "20260910_220000_merge_affinity_invite_heads",
    "20260911_000000_model_source_pins_kind_expires_index",
)
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
