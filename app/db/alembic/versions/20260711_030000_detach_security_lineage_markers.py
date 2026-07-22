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
_DURABLE_LINEAGE_ALIAS_KINDS = ("session_header", "turn_state")
_ANONYMOUS_API_KEY_SCOPE = "__anonymous__"


def _security_lineage_api_key_scope(api_key_scope: str | None) -> str:
    stripped = (api_key_scope or "").strip()
    return stripped or _ANONYMOUS_API_KEY_SCOPE


def _security_lineage_marker_key(security_lineage_id: str, api_key_scope: str | None = None) -> str:
    scope = _security_lineage_api_key_scope(api_key_scope)
    digest = hashlib.sha256(f"{scope}\0{security_lineage_id}".encode("utf-8")).hexdigest()
    return f"{_SECURITY_LINEAGE_MARKER_PREFIX}{digest}"


def _legacy_unscoped_security_lineage_marker_key(security_lineage_id: str) -> str:
    digest = hashlib.sha256(security_lineage_id.encode("utf-8")).hexdigest()
    return f"{_SECURITY_LINEAGE_MARKER_PREFIX}{digest}"


def _detached_security_lineage_marker_source(security_lineage_id: str) -> str | None:
    if security_lineage_id.startswith(_SECURITY_LINEAGE_MARKER_PREFIX):
        return None
    if security_lineage_id.startswith(_LEGACY_SECURITY_LINEAGE_MARKER_PREFIX):
        return security_lineage_id.removeprefix(_LEGACY_SECURITY_LINEAGE_MARKER_PREFIX)
    return security_lineage_id


def _insert_security_lineage_marker_key(bind: sa.engine.Connection, marker_key: str) -> None:
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
            "marker_key": marker_key,
            "kind": _CODEX_SESSION_KIND,
            "requires_security_work_authorized": True,
        },
    )


def _insert_detached_security_lineage_marker(
    bind: sa.engine.Connection,
    security_lineage_id: str,
    *,
    api_key_scope: str | None = None,
    include_legacy_unscoped_marker: bool = False,
) -> None:
    marker_source = _detached_security_lineage_marker_source(security_lineage_id)
    if marker_source is None:
        bind.execute(
            sa.text(
                """
                UPDATE sticky_sessions
                SET account_id = NULL,
                    requires_security_work_authorized = :requires_security_work_authorized,
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = :marker_key
                  AND kind = :kind
                  AND requires_security_work_authorized = :requires_security_work_authorized
                """
            ),
            {
                "marker_key": security_lineage_id,
                "kind": _CODEX_SESSION_KIND,
                "requires_security_work_authorized": True,
            },
        )
        return
    _insert_security_lineage_marker_key(bind, _security_lineage_marker_key(marker_source, api_key_scope))
    if include_legacy_unscoped_marker:
        _insert_security_lineage_marker_key(bind, _legacy_unscoped_security_lineage_marker_key(marker_source))


def _backfill_sticky_security_lineage_markers(
    bind: sa.engine.Connection,
    columns: set[str],
) -> None:
    if not {
        "key",
        "kind",
        "account_id",
        "requires_security_work_authorized",
        "created_at",
        "updated_at",
    }.issubset(columns):
        return
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
        if isinstance(security_lineage_id, str):
            _insert_detached_security_lineage_marker(
                bind,
                security_lineage_id,
                include_legacy_unscoped_marker=True,
            )


def _backfill_bridge_security_lineage_markers(bind: sa.engine.Connection, inspector: sa.Inspector) -> None:
    if not inspector.has_table("http_bridge_sessions"):
        return
    bridge_columns = {column["name"] for column in inspector.get_columns("http_bridge_sessions")}
    if not {
        "id",
        "session_key_kind",
        "session_key_value",
        "requires_security_work_authorized",
    }.issubset(bridge_columns):
        return
    latest_turn_state_expr = "latest_turn_state" if "latest_turn_state" in bridge_columns else "NULL"
    api_key_scope_expr = "api_key_scope" if "api_key_scope" in bridge_columns else f"'{_ANONYMOUS_API_KEY_SCOPE}'"
    rows = bind.execute(
        sa.text(
            f"""
            SELECT
                id,
                session_key_kind,
                session_key_value,
                {api_key_scope_expr} AS api_key_scope,
                {latest_turn_state_expr} AS latest_turn_state
            FROM http_bridge_sessions
            WHERE requires_security_work_authorized = :requires_security_work_authorized
            """
        ),
        {"requires_security_work_authorized": True},
    ).fetchall()
    for _session_id, session_key_kind, session_key_value, api_key_scope, latest_turn_state in rows:
        lineage_api_key_scope = api_key_scope if isinstance(api_key_scope, str) else None
        if session_key_kind in _DURABLE_LINEAGE_ALIAS_KINDS and isinstance(session_key_value, str):
            _insert_detached_security_lineage_marker(
                bind,
                session_key_value,
                api_key_scope=lineage_api_key_scope,
            )
        if isinstance(latest_turn_state, str):
            _insert_detached_security_lineage_marker(bind, latest_turn_state, api_key_scope=lineage_api_key_scope)

    if not rows or not inspector.has_table("http_bridge_session_aliases"):
        return
    alias_columns = {column["name"] for column in inspector.get_columns("http_bridge_session_aliases")}
    if not {"session_id", "alias_kind", "alias_value"}.issubset(alias_columns):
        return
    alias_api_key_scope_expr = (
        "COALESCE(a.api_key_scope, s.api_key_scope, :anonymous_scope)"
        if "api_key_scope" in alias_columns and "api_key_scope" in bridge_columns
        else (
            "COALESCE(a.api_key_scope, :anonymous_scope)"
            if "api_key_scope" in alias_columns
            else (
                "COALESCE(s.api_key_scope, :anonymous_scope)"
                if "api_key_scope" in bridge_columns
                else ":anonymous_scope"
            )
        )
    )
    alias_rows = bind.execute(
        sa.text(
            f"""
            SELECT a.alias_kind, a.alias_value, {alias_api_key_scope_expr} AS api_key_scope
            FROM http_bridge_session_aliases AS a
            JOIN http_bridge_sessions AS s
              ON s.id = a.session_id
            WHERE s.requires_security_work_authorized = :requires_security_work_authorized
              AND a.alias_kind IN :alias_kinds
            """
        ).bindparams(
            sa.bindparam("alias_kinds", expanding=True),
        ),
        {
            "requires_security_work_authorized": True,
            "alias_kinds": list(_DURABLE_LINEAGE_ALIAS_KINDS),
            "anonymous_scope": _ANONYMOUS_API_KEY_SCOPE,
        },
    ).fetchall()
    for _alias_kind, alias_value, api_key_scope in alias_rows:
        if isinstance(alias_value, str):
            _insert_detached_security_lineage_marker(
                bind,
                alias_value,
                api_key_scope=api_key_scope if isinstance(api_key_scope, str) else None,
            )


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
    _backfill_sticky_security_lineage_markers(bind, set(columns))
    _backfill_bridge_security_lineage_markers(bind, inspector)


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
