"""Add dashboard_auth_providers and the invite's expected identity columns.

Revision ID: 20260910_010000_add_dashboard_auth_providers
Revises: 20260909_040000_add_dashboard_user_invites
Create Date: 2026-09-10

One row per sign-in method. The two built-in rows (password, trusted header)
are seeded with "do nothing on conflict" so a re-run never overwrites an
operator's settings. The trusted-header row defaults unknown identities to the
admin preset (D10): an upgraded reverse-proxy install keeps admitting every
identity the proxy vouches for, now as its own account. ``enabled`` is not a
copy of ``CODEX_LB_DASHBOARD_AUTH_MODE``; the mode is read at request time.

``dashboard_user_invites`` gains the nullable ``expected_*`` triple a
pre-created account waits for. Existing invites keep NULL (nothing to link).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.modules.auth_providers.seed import seed_default_auth_providers

revision = "20260910_010000_add_dashboard_auth_providers"
down_revision = "20260910_000000_add_totp_required_for_admin_role"
branch_labels = None
depends_on = None

_TABLE = "dashboard_auth_providers"
_INVITES = "dashboard_user_invites"
_INVITE_COLUMNS: tuple[str, ...] = ("expected_provider", "expected_provider_key", "expected_subject")
_EXPECTED_IDENTITY_INDEX = "uq_dashboard_user_invites_expected_identity"
_OPEN_INVITE = "consumed_at IS NULL AND revoked_at IS NULL"


def _provider_columns() -> tuple[sa.Column, ...]:
    """Fresh Column objects per call: a Column may be bound to one table only."""

    return (
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("unknown_identity_role_id", sa.String(length=36), nullable=True),
        sa.Column("no_match_role_id", sa.String(length=36), nullable=True),
        sa.Column("link_by_email", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("skip_role_sync", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("idp_mfa_enforced", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def _create_providers_table() -> None:
    op.create_table(
        _TABLE,
        *_provider_columns(),
        sa.ForeignKeyConstraint(["unknown_identity_role_id"], ["dashboard_roles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["no_match_role_id"], ["dashboard_roles.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("kind", "provider_key", name="uq_dashboard_auth_providers_kind_key"),
    )


def _seed_table() -> sa.Table:
    """Revision-pinned column list for the seed INSERT (no constraints needed to insert)."""

    return sa.Table(_TABLE, sa.MetaData(), *_provider_columns())


def _invite_columns() -> tuple[sa.Column, ...]:
    """Fresh Column objects per call: a Column may be bound to one table only."""

    return (
        sa.Column("expected_provider", sa.String(length=32), nullable=True),
        sa.Column("expected_provider_key", sa.String(length=128), nullable=True),
        sa.Column("expected_subject", sa.String(length=512), nullable=True),
    )


def _existing_columns(inspector: sa.Inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(_TABLE):
        _create_providers_table()
    # Outside the table guard so a re-run after a partial failure still seeds.
    seed_default_auth_providers(bind, _seed_table())

    existing = _existing_columns(inspector, _INVITES)
    missing = [column for column in _invite_columns() if column.name not in existing]
    if missing:
        with op.batch_alter_table(_INVITES) as batch_op:
            for column in missing:
                batch_op.add_column(column)
    if inspector.has_table(_INVITES) and _EXPECTED_IDENTITY_INDEX not in _index_names(bind):
        # One open invite per expected identity (partial: consumed/revoked rows do not count).
        op.create_index(
            _EXPECTED_IDENTITY_INDEX,
            _INVITES,
            list(_INVITE_COLUMNS),
            unique=True,
            postgresql_where=sa.text(_OPEN_INVITE),
            sqlite_where=sa.text(_OPEN_INVITE),
        )


def _index_names(bind: sa.engine.Connection) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(_INVITES):
        return set()
    return {name for index in inspector.get_indexes(_INVITES) if isinstance(name := index.get("name"), str)}


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _EXPECTED_IDENTITY_INDEX in _index_names(bind):
        op.drop_index(_EXPECTED_IDENTITY_INDEX, table_name=_INVITES)
    present = _existing_columns(inspector, _INVITES)
    to_drop = [name for name in _INVITE_COLUMNS if name in present]
    if to_drop:
        with op.batch_alter_table(_INVITES) as batch_op:
            for name in to_drop:
                batch_op.drop_column(name)
    if inspector.has_table(_TABLE):
        op.drop_table(_TABLE)
