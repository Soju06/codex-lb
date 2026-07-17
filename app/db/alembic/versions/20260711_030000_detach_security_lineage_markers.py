"""Allow security-lineage markers to outlive account rows.

Revision ID: 20260711_030000_detach_security_lineage_markers
Revises: 20260711_020000_add_sticky_session_security_lineage
"""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "20260711_030000_detach_security_lineage_markers"
down_revision = "20260711_020000_add_sticky_session_security_lineage"
branch_labels = None
depends_on = None

_SECURITY_LINEAGE_MARKER_PREFIX = "@security-work/v2/"
_LEGACY_SECURITY_LINEAGE_MARKER_PREFIX = "security-work:"
_CODEX_SESSION_KIND = "codex_session"


def _security_lineage_marker_key(security_lineage_id: str) -> str:
    digest = hashlib.sha256(security_lineage_id.encode("utf-8")).hexdigest()
    return f"{_SECURITY_LINEAGE_MARKER_PREFIX}{digest}"


def _security_lineage_id_uses_reserved_namespace(security_lineage_id: str) -> bool:
    return security_lineage_id.startswith((_SECURITY_LINEAGE_MARKER_PREFIX, _LEGACY_SECURITY_LINEAGE_MARKER_PREFIX))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sticky_sessions"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("sticky_sessions")}
    account_id = columns.get("account_id")
    if account_id is not None and not account_id.get("nullable", False):
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.alter_column("account_id", existing_type=sa.String(), nullable=True)
    if {
        "key",
        "kind",
        "account_id",
        "requires_security_work_authorized",
        "created_at",
        "updated_at",
    }.issubset(columns):
        rows = bind.execute(
            sa.text(
                """
                SELECT key
                FROM sticky_sessions
                WHERE kind = :kind
                  AND account_id IS NOT NULL
                  AND requires_security_work_authorized = :requires_security_work_authorized
                """
            ),
            {"kind": _CODEX_SESSION_KIND, "requires_security_work_authorized": True},
        ).fetchall()
        for (security_lineage_id,) in rows:
            if not isinstance(security_lineage_id, str) or _security_lineage_id_uses_reserved_namespace(
                security_lineage_id
            ):
                continue
            bind.execute(
                sa.text(
                    """
                    INSERT INTO sticky_sessions (
                        key,
                        kind,
                        account_id,
                        requires_security_work_authorized,
                        created_at,
                        updated_at
                    )
                    SELECT
                        :marker_key,
                        :kind,
                        NULL,
                        :requires_security_work_authorized,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM sticky_sessions
                        WHERE key = :marker_key
                          AND kind = :kind
                    )
                    """
                ),
                {
                    "marker_key": _security_lineage_marker_key(security_lineage_id),
                    "kind": _CODEX_SESSION_KIND,
                    "requires_security_work_authorized": True,
                },
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("sticky_sessions"):
        return
    columns = {column["name"]: column for column in inspector.get_columns("sticky_sessions")}
    account_id = columns.get("account_id")
    if account_id is not None and account_id.get("nullable", False):
        op.execute(sa.text("DELETE FROM sticky_sessions WHERE account_id IS NULL"))
        with op.batch_alter_table("sticky_sessions") as batch_op:
            batch_op.alter_column("account_id", existing_type=sa.String(), nullable=False)
