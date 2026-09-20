"""Add the SCIM bearer-token table and the identity's pushed user name.

Revision ID: 20260914_000000_add_scim_tokens
Revises: 20260913_000000_add_oidc_provider_flow
Create Date: 2026-09-14

``dashboard_scim_tokens`` holds only the SHA-256 digest of each secret, in a
unique index, so verification is an index equality and no readable copy of the
credential survives the response that issued it. ``provider_key`` is the
identity namespace the token owns; it is read from the row on every request and
never from the caller.

``dashboard_identities.user_name`` is nullable and written only by the SCIM
path: our usernames are slugs without ``@``, so a ``userName eq`` filter on the
identity provider's own spelling can never match one, and an identity provider
reconciling the resources it created would otherwise find none of them.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260914_000000_add_scim_tokens"
down_revision = "20260913_000000_add_oidc_provider_flow"
branch_labels = None
depends_on = None

_TOKENS = "dashboard_scim_tokens"
_IDENTITIES = "dashboard_identities"
_USER_NAME_COLUMN = "user_name"
_CREATED_BY_FK = "fk_dashboard_scim_tokens_created_by_user_id"


def _existing_columns(inspector: sa.Inspector, table: str) -> set[str]:
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table(_TOKENS):
        # Constraints are declared inline: SQLite cannot add them afterwards.
        op.create_table(
            _TOKENS,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("label", sa.String(length=64), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
            sa.Column("token_prefix", sa.String(length=32), nullable=False),
            sa.Column("provider_key", sa.String(length=128), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["created_by_user_id"],
                ["dashboard_users.id"],
                ondelete="SET NULL",
                name=_CREATED_BY_FK,
            ),
        )

    if _USER_NAME_COLUMN not in _existing_columns(inspector, _IDENTITIES):
        with op.batch_alter_table(_IDENTITIES) as batch_op:
            batch_op.add_column(sa.Column(_USER_NAME_COLUMN, sa.String(length=256), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _USER_NAME_COLUMN in _existing_columns(inspector, _IDENTITIES):
        with op.batch_alter_table(_IDENTITIES) as batch_op:
            batch_op.drop_column(_USER_NAME_COLUMN)
    if inspector.has_table(_TOKENS):
        op.drop_table(_TOKENS)
