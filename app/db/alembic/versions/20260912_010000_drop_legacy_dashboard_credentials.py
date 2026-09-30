"""Drop the legacy dashboard credential columns from ``dashboard_settings``.

Revision ID: 20260912_010000_drop_legacy_dashboard_credentials
Revises: 20260912_000000_merge_thread_cache_and_bridge_retirement_heads
Create Date: 2026-09-12

``dashboard_users`` has been the only authority for dashboard credentials since
``20260909_020000_reproject_compat_admin_credentials`` copied the legacy values
onto the account row; the three columns below were kept one release longer as a
write-only projection so a replica of the previous release kept working. That
projection is removed in this release, so the columns are now an unowned copy of
a live password hash and an encrypted TOTP secret that no code path clears or
rotates -- which is why they go in the same change.

**This upgrade is not rolling-safe.** Every replica of an earlier release maps
these columns and loads the settings row as a whole entity, so its settings
reads (and, on release N, its credential mirror) fail the moment the columns are
gone. Old replicas must be stopped before this runs, and ``run_upgrade`` says so
out loud once. It refuses nothing: this revision descends from the reprojection,
so a database stamped at any older revision re-projects and only then drops, in
one command.

The downgrade re-creates the columns and re-projects them from the account row
carrying the deterministic compat id -- by id, because after this release the
bootstrap account's *name* is no longer a stable way to find it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260912_010000_drop_legacy_dashboard_credentials"
down_revision = "20260912_000000_merge_thread_cache_and_bridge_retirement_heads"
branch_labels = None
depends_on = None

_TABLE = "dashboard_settings"
_USERS_TABLE = "dashboard_users"
_SENTINELS_TABLE = "runtime_sentinels"

#: Durable "the legacy credential layer is retired" marker, read by
#: ``app.db.migrate.run_upgrade`` (which freezes the same literal, because
#: neither side may import the other).
#:
#: The ledger cannot carry this, because the ledger is the thing that goes
#: wrong: a database whose ledger was lost or rewound has the chain replayed
#: over it, which re-creates these three columns *empty* a few revisions before
#: dropping them again -- and the one-shot re-projection in between reads that
#: emptiness as "the password was removed" and clears the account row, which by
#: then holds the only credential the install has. A guard inside that
#: re-projection cannot help: it is a published revision, so an install that
#: already applied it never applies it again, and editing it would give one
#: revision id two histories. ``run_upgrade`` sees the marker first and stamps
#: the ledger at this revision instead of replaying over the schema. Only this
#: revision writes the marker and only its downgrade removes it, so the marker
#: means these columns are gone whatever the ledger claims.
_RETIRED_SENTINEL = "dashboard_legacy_credentials_retired"

#: Frozen copy of the runtime identifier (see 20260909_010000): a migration must
#: not import transient application modules.
COMPAT_ADMIN_USER_ID = "7a4fc02d-216e-5be7-b974-bc437f23df60"

#: The three the projection wrote, and the only three that go. The guest
#: credential, both bootstrap-token columns, both TOTP requirement flags and
#: ``local_login_policy`` are live and stay.
_DROPPED_COLUMN_NAMES: tuple[str, ...] = (
    "password_hash",
    "totp_secret_encrypted",
    "totp_last_verified_step",
)


def _columns(connection: Connection, table: str) -> set[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def _set_retired_marker(bind: Connection, *, retired: bool) -> None:
    if not sa.inspect(bind).has_table(_SENTINELS_TABLE):
        return
    bind.execute(sa.text(f"DELETE FROM {_SENTINELS_TABLE} WHERE name = :name"), {"name": _RETIRED_SENTINEL})
    if retired:
        bind.execute(
            sa.text(f"INSERT INTO {_SENTINELS_TABLE} (name, value) VALUES (:name, :value)"),
            {"name": _RETIRED_SENTINEL, "value": revision},
        )


def upgrade() -> None:
    bind = op.get_bind()
    present = _columns(bind, _TABLE)
    doomed = [name for name in _DROPPED_COLUMN_NAMES if name in present]
    if doomed:
        with op.batch_alter_table(_TABLE) as batch_op:
            for name in doomed:
                batch_op.drop_column(name)
    _set_retired_marker(bind, retired=True)


def downgrade() -> None:
    bind = op.get_bind()
    _set_retired_marker(bind, retired=False)
    present = _columns(bind, _TABLE)
    if present and not {*_DROPPED_COLUMN_NAMES} <= present:
        with op.batch_alter_table(_TABLE) as batch_op:
            if "password_hash" not in present:
                batch_op.add_column(sa.Column("password_hash", sa.Text(), nullable=True))
            if "totp_secret_encrypted" not in present:
                batch_op.add_column(sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True))
            if "totp_last_verified_step" not in present:
                batch_op.add_column(sa.Column("totp_last_verified_step", sa.Integer(), nullable=True))

    if not _columns(bind, _TABLE) or not _columns(bind, _USERS_TABLE):
        return
    account = bind.execute(
        sa.text(
            f"SELECT password_hash, totp_secret_encrypted, totp_last_verified_step FROM {_USERS_TABLE} WHERE id = :id"
        ),
        {"id": COMPAT_ADMIN_USER_ID},
    ).first()
    if account is None:
        # Nothing deterministic to project from (the account was deleted, or
        # this install never bootstrapped one): leave the columns NULL.
        return
    bind.execute(
        sa.text(
            f"UPDATE {_TABLE} SET password_hash = :password_hash, "
            "totp_secret_encrypted = :totp_secret_encrypted, "
            "totp_last_verified_step = :totp_last_verified_step WHERE id = 1"
        ),
        {
            "password_hash": account[0],
            "totp_secret_encrypted": account[1],
            "totp_last_verified_step": account[2],
        },
    )
