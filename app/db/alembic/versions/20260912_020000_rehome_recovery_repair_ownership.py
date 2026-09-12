"""Move legacy recovery-repair ownership markers to their real migrations.

The first version of the recovery repair recorded every restored object under
its own revision.  That revision has already been stamped on deployed
databases, so changing its upgrade code cannot rewrite those rows.  This
follow-up runs on the next upgrade and attributes each object to the
historical migration that owns it, allowing later downgrades to remove only
objects that are truly beyond the requested target.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.alembic.http_bridge_migration_ownership import OWNERSHIP_TABLE, ensure_ownership_table

revision = "20260912_020000_rehome_recovery_repair_ownership"
down_revision = "20260912_010000_merge_recovery_repair_and_thread_cache_heads"
branch_labels = None
depends_on = None

_LEGACY_REPAIR_REVISION = "20260911_070000_repair_http_bridge_recovery_columns"
_OBJECT_OWNERS = (
    ("column", "rebind_claim_id", "20260906_000000_add_http_bridge_rebind_claim"),
    ("column", "transcript_version", "20260821_010000_add_http_bridge_complete_transcript"),
    ("column", "response_output_items_json", "20260821_010000_add_http_bridge_complete_transcript"),
    ("column", "response_output_items_complete", "20260821_010000_add_http_bridge_complete_transcript"),
    ("column", "response_replay_input_json", "20260821_020000_add_http_bridge_replay_snapshot"),
    ("column", "response_replay_input_complete", "20260821_020000_add_http_bridge_replay_snapshot"),
    ("column", "response_replay_input_turn_count", "20260828_010000_add_http_bridge_replay_turn_count"),
    (
        "index",
        "idx_http_bridge_operations_session_state_created",
        "20260815_000000_add_http_bridge_recent_unknown_index",
    ),
    (
        "index",
        "idx_http_bridge_operations_response_state",
        "20260821_010000_add_http_bridge_complete_transcript",
    ),
    ("column", "target_response_id", "20260827_000000_add_http_bridge_retained_alias_target"),
)


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(OWNERSHIP_TABLE):
        return

    ensure_ownership_table(bind)
    table = sa.table(
        OWNERSHIP_TABLE,
        sa.column("revision", sa.String(128)),
        sa.column("object_type", sa.String(32)),
        sa.column("object_name", sa.String(128)),
    )
    for object_type, object_name, owner_revision in _OBJECT_OWNERS:
        legacy_marker = sa.and_(
            table.c.revision == _LEGACY_REPAIR_REVISION,
            table.c.object_type == object_type,
            table.c.object_name == object_name,
        )
        if bind.execute(sa.select(table.c.revision).where(legacy_marker)).first() is None:
            continue
        owner_marker = sa.and_(
            table.c.revision == owner_revision,
            table.c.object_type == object_type,
            table.c.object_name == object_name,
        )
        if bind.execute(sa.select(table.c.revision).where(owner_marker)).first() is None:
            bind.execute(
                sa.insert(table).values(
                    revision=owner_revision,
                    object_type=object_type,
                    object_name=object_name,
                )
            )
        bind.execute(sa.delete(table).where(legacy_marker))


def downgrade() -> None:
    """Keep the historical ownership attribution when moving below this merge."""
