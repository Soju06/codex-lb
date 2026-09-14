"""Add the OIDC login-flow table, the provider's test-login proof and its seeded row.

Revision ID: 20260913_000000_add_oidc_provider_flow
Revises: 20260912_010000_drop_legacy_dashboard_credentials
Create Date: 2026-09-13

The connection settings themselves need no column: ``config_encrypted`` was
reserved for them when the providers table landed. What is new is durable
*flow* state -- the callback lands on whichever replica the load balancer picks,
so the state, the nonce hash and the PKCE verifier cannot live in memory -- and
the two columns that record an admin's pre-flight test login.

The ``oidc/default`` row is seeded disabled with no role for an unmatched
identity, through the same insert-ignore helper the other built-in rows use, so
an install that never connects an identity provider is unchanged and a
connected one denies unknown identities until an operator says otherwise.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.modules.auth_providers.seed import seed_default_auth_providers

revision = "20260913_000000_add_oidc_provider_flow"
down_revision = "20260912_010000_drop_legacy_dashboard_credentials"
branch_labels = None
depends_on = None

_PROVIDERS = "dashboard_auth_providers"
_FLOWS = "dashboard_oidc_login_flows"
_FLOWS_INDEX = "idx_dashboard_oidc_login_flows_expires_at"
_PROOF_COLUMNS: tuple[str, ...] = ("test_login_user_id", "test_login_verified_at")
_PROOF_FK = "fk_dashboard_auth_providers_test_login_user_id"


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


def _seed_table() -> sa.Table:
    """Revision-pinned column list for the seed INSERT (no constraints needed to insert)."""

    return sa.Table(_PROVIDERS, sa.MetaData(), *_provider_columns())


def _proof_columns() -> tuple[sa.Column, ...]:
    """Fresh Column objects per call: a Column may be bound to one table only."""

    return (
        sa.Column(
            "test_login_user_id",
            sa.String(length=36),
            # Named because SQLite's batch mode rebuilds the table and refuses
            # to re-add an anonymous constraint.
            sa.ForeignKey("dashboard_users.id", ondelete="SET NULL", name=_PROOF_FK),
            nullable=True,
        ),
        sa.Column("test_login_verified_at", sa.DateTime(timezone=True), nullable=True),
    )


def _existing_columns(inspector: sa.Inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing = _existing_columns(inspector, _PROVIDERS)
    missing = [column for column in _proof_columns() if column.name not in existing]
    if missing:
        with op.batch_alter_table(_PROVIDERS) as batch_op:
            for column in missing:
                batch_op.add_column(column)

    if not inspector.has_table(_FLOWS):
        # Constraints are declared inline: SQLite cannot add them afterwards.
        op.create_table(
            _FLOWS,
            sa.Column("state_hash", sa.String(length=64), primary_key=True),
            sa.Column("provider_id", sa.String(length=36), nullable=False),
            sa.Column("nonce_hash", sa.String(length=64), nullable=False),
            sa.Column("code_verifier_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("purpose", sa.String(length=16), nullable=False),
            sa.Column("acting_user_id", sa.String(length=36), nullable=True),
            sa.Column("redirect_uri", sa.String(length=512), nullable=False),
            # The connection document this flow was started against, so a
            # completed pre-flight can only stamp the configuration it proved.
            sa.Column("config_fingerprint", sa.String(length=64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["provider_id"], [f"{_PROVIDERS}.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["acting_user_id"], ["dashboard_users.id"], ondelete="CASCADE"),
        )
        op.create_index(_FLOWS_INDEX, _FLOWS, ["expires_at"])

    # Outside the guards so a re-run after a partial failure still seeds, and
    # insert-ignore so an operator's configuration survives the upgrade.
    if inspector.has_table(_PROVIDERS):
        seed_default_auth_providers(bind, _seed_table())


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table(_FLOWS):
        op.drop_table(_FLOWS)
    present = _existing_columns(inspector, _PROVIDERS)
    to_drop = [name for name in _PROOF_COLUMNS if name in present]
    if to_drop:
        with op.batch_alter_table(_PROVIDERS) as batch_op:
            for name in to_drop:
                batch_op.drop_column(name)
