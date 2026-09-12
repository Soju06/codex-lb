"""add subscription overflow designation and model source pins

Schema-only groundwork for subscription-exhaustion overflow to a designated
model source (#2123, WP-A): two nullable ``dashboard_settings`` columns (the
designated source id and the drain deadline armed when that designation is
cleared) and the ``model_source_pins`` table later stages use to keep
conversations sticky to the source they overflowed to. Nothing reads the new
columns or table yet; this revision is inert.

Revision ID: 20260908_000000_add_subscription_overflow
Revises: 20260830_000000_add_quota_warmup_claim_expiry
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection
from sqlalchemy.engine.interfaces import ReflectedIndex

revision = "20260908_000000_add_subscription_overflow"
down_revision = "20260830_000000_add_quota_warmup_claim_expiry"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_PINS_TABLE = "model_source_pins"
_PINS_INDEX = "ix_model_source_pins_purge_at"


def _settings_columns() -> tuple[sa.Column, ...]:
    # Fresh Column objects per call: a Column binds to the table it is added to,
    # so module-level instances could not be reused across upgrades in one process.
    return (
        # No foreign key on purpose: a dangling id means "off" (precedent:
        # single_account_id).
        sa.Column("subscription_overflow_source_id", sa.String(), nullable=True),
        # Naive UTC like every other dashboard_settings timestamp; the app
        # compares it against utcnow().
        sa.Column("subscription_overflow_drain_until", sa.DateTime(), nullable=True),
    )


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _reflected_index(connection: Connection, table_name: str, index_name: str) -> ReflectedIndex | None:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return None
    for index in inspector.get_indexes(table_name):
        if index.get("name") == index_name:
            return index
    return None


def _index_targets_purge_at(index: ReflectedIndex) -> bool:
    # A same-named index on other columns (or a unique one) is a decoy: it must
    # be replaced, not accepted by name.
    return tuple(index.get("column_names") or ()) == ("purge_at",) and not index.get("unique")


def _postgresql_index_is_invalid(connection: Connection, index_name: str) -> bool:
    return bool(
        connection.execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": index_name},
        ).scalar()
    )


def upgrade() -> None:
    bind = op.get_bind()

    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    missing_columns = [column for column in _settings_columns() if column.name not in existing_columns]
    if existing_columns and missing_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            for column in missing_columns:
                batch_op.add_column(column)

    if not sa.inspect(bind).has_table(_PINS_TABLE):
        op.create_table(
            _PINS_TABLE,
            sa.Column("pin_key", sa.String(), nullable=False),
            # thread | anchor | bounce, a plain string: the routing stage owns the values.
            sa.Column("kind", sa.String(), nullable=False),
            # No foreign key on purpose: rows must outlive a deleted source for
            # the drain window instead of cascading away.
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            # Timezone-aware like file_account_pins: the database clock is
            # authoritative for pin expiry and tombstone comparisons.
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("purge_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("pin_key"),
        )

    # Guarded independently of the table step: a database where the table
    # already exists (partial dump, out-of-band creation) still needs the
    # purge-at index. The table is created empty by this same revision, so a
    # plain in-transaction index suffices; CONCURRENTLY would buy nothing.
    # An existing same-named index is only accepted when it is the index this
    # revision would create: one on exactly ``purge_at`` that is, on
    # PostgreSQL, not left invalid by an interrupted out-of-band CONCURRENTLY
    # build. Anything else is dropped and rebuilt.
    existing_index = _reflected_index(bind, _PINS_TABLE, _PINS_INDEX)
    if existing_index is not None:
        if _index_targets_purge_at(existing_index) and (
            bind.dialect.name != "postgresql" or not _postgresql_index_is_invalid(bind, _PINS_INDEX)
        ):
            return
        op.drop_index(_PINS_INDEX, table_name=_PINS_TABLE)
    op.create_index(_PINS_INDEX, _PINS_TABLE, ["purge_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()

    if sa.inspect(bind).has_table(_PINS_TABLE):
        if _reflected_index(bind, _PINS_TABLE, _PINS_INDEX) is not None:
            op.drop_index(_PINS_INDEX, table_name=_PINS_TABLE)
        op.drop_table(_PINS_TABLE)

    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    present_columns = [column.name for column in _settings_columns() if column.name in existing_columns]
    if present_columns:
        with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
            for column_name in present_columns:
                batch_op.drop_column(column_name)
