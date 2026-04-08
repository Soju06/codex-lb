"""rekey sticky sessions by provider

Revision ID: 20260407_030000_rekey_sticky_sessions_by_provider
Revises: 20260407_020000_add_openai_platform_identities
Create Date: 2026-04-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection

# revision identifiers, used by Alembic.
revision = "20260407_030000_rekey_sticky_sessions_by_provider"
down_revision = "20260407_020000_add_openai_platform_identities"
branch_labels = None
depends_on = None

_TABLE_NAME = "sticky_sessions"
_TEMP_TABLE_NAME = "sticky_sessions__provider_rekey"
_TARGET_PK_COLUMNS = ["provider_kind", "kind", "key"]
_LEGACY_TEMP_TABLE_NAME = "sticky_sessions__provider_rekey_downgrade"


def _table_exists(connection: Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return inspector.has_table(table_name)


def _primary_key_columns(connection: Connection, table_name: str) -> list[str]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return []
    constraint = inspector.get_pk_constraint(table_name) or {}
    columns = constraint.get("constrained_columns") or []
    return [str(column) for column in columns]


def _sticky_session_kind_enum(connection: Connection, *, create_type: bool = True) -> sa.Enum:
    if connection.dialect.name == "postgresql":
        return postgresql.ENUM(
            "codex_session",
            "sticky_thread",
            "prompt_cache",
            name="sticky_session_kind",
            create_type=create_type,
        )
    return sa.Enum(
        "codex_session",
        "sticky_thread",
        "prompt_cache",
        name="sticky_session_kind",
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, _TABLE_NAME):
        return
    if _primary_key_columns(bind, _TABLE_NAME) == _TARGET_PK_COLUMNS:
        return

    if bind.dialect.name == "postgresql":
        _sticky_session_kind_enum(bind).create(bind, checkfirst=True)

    op.create_table(
        _TEMP_TABLE_NAME,
        sa.Column("provider_kind", sa.String(), nullable=False, server_default=sa.text("'chatgpt_web'")),
        sa.Column(
            "kind",
            _sticky_session_kind_enum(bind, create_type=False),
            nullable=False,
            server_default=sa.text("'sticky_thread'"),
        ),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("routing_subject_id", sa.String(), nullable=False),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("provider_kind", "kind", "key"),
        sa.CheckConstraint("routing_subject_id <> ''", name="ck_sticky_sessions_routing_subject_non_empty"),
        sa.CheckConstraint(
            "account_id IS NULL OR provider_kind = 'chatgpt_web'",
            name="ck_sticky_sessions_account_scope",
        ),
        sa.CheckConstraint(
            "NOT (provider_kind = 'openai_platform' AND kind = 'codex_session')",
            name="ck_sticky_sessions_platform_codex_session",
        ),
    )
    invalid_row_count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT
                    COALESCE(NULLIF(provider_kind, ''), 'chatgpt_web') AS provider_kind,
                    COALESCE(kind, 'sticky_thread') AS kind,
                    key,
                    COALESCE(NULLIF(routing_subject_id, ''), account_id) AS routing_subject_id,
                    CASE
                        WHEN COALESCE(NULLIF(provider_kind, ''), 'chatgpt_web') = 'chatgpt_web'
                        THEN account_id
                        ELSE NULL
                    END AS account_id
                FROM {_TABLE_NAME}
            ) AS normalized
            WHERE normalized.key IS NULL
               OR normalized.key = ''
               OR normalized.routing_subject_id IS NULL
               OR normalized.routing_subject_id = ''
               OR normalized.provider_kind NOT IN ('chatgpt_web', 'openai_platform')
               OR (
                    normalized.provider_kind = 'chatgpt_web'
                    AND normalized.account_id IS NULL
               )
               OR (
                    normalized.provider_kind = 'openai_platform'
                    AND normalized.kind = 'codex_session'
               )
            """
        )
    ).scalar_one()
    if int(invalid_row_count or 0) > 0:
        op.drop_table(_TEMP_TABLE_NAME)
        raise RuntimeError(
            "Refusing to rekey sticky_sessions with invalid legacy rows; clean them up before running "
            "20260407_030000_rekey_sticky_sessions_by_provider."
        )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_TEMP_TABLE_NAME} (
                provider_kind,
                kind,
                key,
                routing_subject_id,
                account_id,
                created_at,
                updated_at
            )
            SELECT
                normalized.provider_kind,
                normalized.kind,
                normalized.key,
                normalized.routing_subject_id,
                normalized.account_id,
                normalized.created_at,
                normalized.updated_at
            FROM (
                SELECT
                    COALESCE(NULLIF(provider_kind, ''), 'chatgpt_web') AS provider_kind,
                    COALESCE(kind, 'sticky_thread') AS kind,
                    key,
                    COALESCE(NULLIF(routing_subject_id, ''), account_id) AS routing_subject_id,
                    CASE
                        WHEN COALESCE(NULLIF(provider_kind, ''), 'chatgpt_web') = 'chatgpt_web'
                        THEN account_id
                        ELSE NULL
                    END AS account_id,
                    created_at,
                    updated_at
                FROM {_TABLE_NAME}
            ) AS normalized
            WHERE normalized.key IS NOT NULL
              AND normalized.key <> ''
              AND normalized.routing_subject_id IS NOT NULL
              AND normalized.routing_subject_id <> ''
              AND normalized.provider_kind IN ('chatgpt_web', 'openai_platform')
              AND NOT (
                  normalized.provider_kind = 'chatgpt_web'
                  AND normalized.account_id IS NULL
              )
              AND NOT (
                  normalized.provider_kind = 'openai_platform'
                  AND normalized.kind = 'codex_session'
              )
            """
        )
    )
    op.drop_table(_TABLE_NAME)
    op.rename_table(_TEMP_TABLE_NAME, _TABLE_NAME)
    op.create_index("idx_sticky_account", _TABLE_NAME, ["account_id"], unique=False)
    op.execute(sa.text("CREATE INDEX idx_sticky_kind_updated_at ON sticky_sessions (kind, updated_at DESC)"))
    op.create_index(
        "idx_sticky_provider_routing_kind",
        _TABLE_NAME,
        ["provider_kind", "routing_subject_id", "kind"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, _TABLE_NAME):
        return
    if _primary_key_columns(bind, _TABLE_NAME) == ["key", "kind"]:
        return

    incompatible_row_count = bind.execute(
        sa.text(
            f"""
            SELECT COUNT(*)
            FROM {_TABLE_NAME}
            WHERE provider_kind <> 'chatgpt_web'
               OR account_id IS NULL
               OR account_id = ''
            """
        )
    ).scalar_one()
    if int(incompatible_row_count or 0) > 0:
        raise RuntimeError(
            "Refusing to downgrade provider-scoped sticky_sessions while non-ChatGPT rows exist; "
            "clean them up before rolling back 20260407_030000_rekey_sticky_sessions_by_provider."
        )

    if bind.dialect.name == "postgresql":
        _sticky_session_kind_enum(bind).create(bind, checkfirst=True)

    op.create_table(
        _LEGACY_TEMP_TABLE_NAME,
        sa.Column("key", sa.String(), nullable=False),
        sa.Column(
            "kind",
            _sticky_session_kind_enum(bind, create_type=False),
            nullable=False,
            server_default=sa.text("'sticky_thread'"),
        ),
        sa.Column("account_id", sa.String(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider_kind", sa.String(), nullable=False, server_default=sa.text("'chatgpt_web'")),
        sa.Column("routing_subject_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("key", "kind"),
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO {_LEGACY_TEMP_TABLE_NAME} (
                key,
                kind,
                account_id,
                provider_kind,
                routing_subject_id,
                created_at,
                updated_at
            )
            SELECT
                key,
                kind,
                account_id,
                provider_kind,
                routing_subject_id,
                created_at,
                updated_at
            FROM {_TABLE_NAME}
            WHERE provider_kind = 'chatgpt_web'
            """
        )
    )
    op.drop_table(_TABLE_NAME)
    op.rename_table(_LEGACY_TEMP_TABLE_NAME, _TABLE_NAME)
    op.create_index("idx_sticky_account", _TABLE_NAME, ["account_id"], unique=False)
    op.execute(sa.text("CREATE INDEX idx_sticky_kind_updated_at ON sticky_sessions (kind, updated_at DESC)"))
    op.create_index(
        "idx_sticky_provider_routing",
        _TABLE_NAME,
        ["provider_kind", "routing_subject_id"],
        unique=False,
    )
