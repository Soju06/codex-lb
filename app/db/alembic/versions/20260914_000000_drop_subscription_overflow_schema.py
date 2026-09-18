"""Drop the subscription-exhaustion overflow schema.

The feature was withdrawn (#2123): a 60-body capture sweep of real Codex
traffic found nothing the portability predicate would ever pass, and making it
pass would mean translating request bodies per provider. The code went in the
preceding revert; this revision takes the storage with it.

Production never wrote a row -- ``model_source_pins`` was empty, both
``dashboard_settings`` columns were NULL -- so the upgrade cannot lose data.
Every step is guarded so a database missing either object (a partial dump, a
fresh install created from current metadata) upgrades cleanly. The downgrade
restores exactly what ``20260908_000000_add_subscription_overflow`` and
``20260911_000000_model_source_pins_kind_expires_index`` built, so the pair
round-trips.

**This upgrade is not rolling-safe.** Every release below this one maps both
``dashboard_settings`` columns and loads the settings row as a whole entity, so
its settings reads fail the moment the columns are gone -- and the chart's
migration Job is a ``pre-upgrade`` hook, which runs before the new pods roll and
therefore before the old ones drain. Pre-withdrawal replicas must be stopped
before this runs; ``docs/deployment/kubernetes.md`` carries the procedure. It
refuses nothing, and unlike ``20260912_010000_drop_legacy_dashboard_credentials``
it says nothing either: that revision's pre-DDL drain warning in
``app/db/migrate.py`` is written around the credential columns it protects.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260914_000000_drop_subscription_overflow_schema"
down_revision = "20260913_000000_add_oidc_provider_flow"
branch_labels = None
depends_on = None

_SETTINGS_TABLE = "dashboard_settings"
_PINS_TABLE = "model_source_pins"
_PURGE_INDEX = "ix_model_source_pins_purge_at"
_KIND_EXPIRES_INDEX = "ix_model_source_pins_kind_expires_at"


def _settings_columns() -> tuple[sa.Column, ...]:
    # Fresh Column objects per call: a Column binds to the table it is added
    # to, so module-level instances could not be reused across one process.
    return (
        sa.Column("subscription_overflow_source_id", sa.String(), nullable=True),
        sa.Column("subscription_overflow_drain_until", sa.DateTime(), nullable=True),
    )


def _column_names(connection: Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(column["name"]) for column in inspector.get_columns(table_name) if column.get("name") is not None}


def _index_names(connection: Connection, table_name: str) -> set[str]:
    # A fresh inspector per call, because the caller may have created the table
    # after an earlier reflection filled another inspector's cache.
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return set()
    return {str(index["name"]) for index in inspector.get_indexes(table_name) if index.get("name") is not None}


def _add_columns(names: Sequence[sa.Column]) -> None:
    with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
        for column in names:
            batch_op.add_column(column)


def _drop_columns(names: Sequence[str]) -> None:
    with op.batch_alter_table(_SETTINGS_TABLE) as batch_op:
        for column_name in names:
            batch_op.drop_column(column_name)


def upgrade() -> None:
    bind = op.get_bind()

    # DROP TABLE takes both indexes with it, so they need no separate step --
    # and dropping them first would only widen the window where the table is
    # unindexed. The table is empty, so the exclusive lock is instant.
    if sa.inspect(bind).has_table(_PINS_TABLE):
        op.drop_table(_PINS_TABLE)

    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    present = [column.name for column in _settings_columns() if column.name in existing_columns]
    if present:
        _drop_columns(present)


def downgrade() -> None:
    bind = op.get_bind()

    existing_columns = _column_names(bind, _SETTINGS_TABLE)
    missing = [column for column in _settings_columns() if column.name not in existing_columns]
    if existing_columns and missing:
        _add_columns(missing)

    if not sa.inspect(bind).has_table(_PINS_TABLE):
        op.create_table(
            _PINS_TABLE,
            sa.Column("pin_key", sa.String(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            # No foreign key, as in the original: rows had to outlive a deleted
            # source for the drain window instead of cascading away.
            sa.Column("source_id", sa.String(), nullable=False),
            sa.Column("api_key_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("purge_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("pin_key"),
        )

    # Each index is reflected on its own rather than ridden along with CREATE
    # TABLE, exactly as 20260908_000000_add_subscription_overflow does it: a
    # downgrade interrupted between the table and its indexes -- or a partial
    # restore that already carries the table -- would otherwise land stamped at
    # the parent with neither index, and nothing later would build them.
    # Both tables are empty here, so the plain in-transaction build is right on
    # either dialect; CONCURRENTLY would buy nothing and cannot run in this
    # transaction.
    existing_indexes = _index_names(bind, _PINS_TABLE)
    if _PURGE_INDEX not in existing_indexes:
        op.create_index(_PURGE_INDEX, _PINS_TABLE, ["purge_at"], unique=False)
    if _KIND_EXPIRES_INDEX not in existing_indexes:
        op.create_index(_KIND_EXPIRES_INDEX, _PINS_TABLE, ["kind", "expires_at"], unique=False)
