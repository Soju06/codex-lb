"""Reconcile durable security-lineage persistence without a second head.

Revision ID: 20260722_000000_add_security_lineage_persistence
Revises: 20260722_000000_backfill_request_log_useragent_families
Create Date: 2026-07-22 00:00:00.000000
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "20260722_000000_add_security_lineage_persistence"
down_revision = "20260722_000000_backfill_request_log_useragent_families"
branch_labels = None
depends_on = None

_MARKER_PREFIX = "@security-work/v2/"
_LEGACY_MARKER_PREFIX = "security-work:"
_CODEX_SESSION_KIND = "codex_session"
_LINEAGE_ALIAS_KINDS = ("session_header", "turn_state", "previous_response_id")
_ANONYMOUS_SCOPE = "__anonymous__"
_BATCH_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _columns(connection: Connection, table_name: str) -> dict[str, Mapping[str, object]]:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return {}
    return {str(column["name"]): column for column in inspector.get_columns(table_name) if column.get("name")}


def _account_foreign_key(connection: Connection, table_name: str) -> Mapping[str, object] | None:
    inspector = sa.inspect(connection)
    if not inspector.has_table(table_name):
        return None
    for foreign_key in inspector.get_foreign_keys(table_name):
        if foreign_key.get("constrained_columns") == ["account_id"] and foreign_key.get("referred_table") == "accounts":
            return foreign_key
    return None


def _marker_key(lineage_id: str, api_key_scope: str | None) -> str:
    scope = (api_key_scope or "").strip() or _ANONYMOUS_SCOPE
    digest = hashlib.sha256(f"{scope}\0{lineage_id}".encode()).hexdigest()
    return f"{_MARKER_PREFIX}{digest}"


def _legacy_marker_key(lineage_id: str) -> str:
    return f"{_MARKER_PREFIX}{hashlib.sha256(lineage_id.encode()).hexdigest()}"


def _insert_marker(bind: Connection, marker_key: str) -> None:
    bind.execute(
        sa.text(
            """
            INSERT INTO sticky_sessions (
                key, kind, account_id, requires_security_work_authorized, created_at, updated_at
            )
            SELECT :key, :kind, NULL, :required, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM sticky_sessions WHERE key = :key AND kind = :kind
            )
            """
        ),
        {"key": marker_key, "kind": _CODEX_SESSION_KIND, "required": True},
    )
    bind.execute(
        sa.text(
            """
            UPDATE sticky_sessions
            SET account_id = NULL, requires_security_work_authorized = :required,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = :key AND kind = :kind
            """
        ),
        {"key": marker_key, "kind": _CODEX_SESSION_KIND, "required": True},
    )


def _backfill_marker(bind: Connection, lineage_id: str, api_key_scope: str | None, *, legacy: bool = False) -> None:
    if lineage_id.startswith(_MARKER_PREFIX):
        bind.execute(
            sa.text(
                """
                UPDATE sticky_sessions
                SET account_id = NULL, requires_security_work_authorized = :required, updated_at = CURRENT_TIMESTAMP
                WHERE key = :key AND kind = :kind
                """
            ),
            {"key": lineage_id, "kind": _CODEX_SESSION_KIND, "required": True},
        )
        return
    if lineage_id.startswith(_LEGACY_MARKER_PREFIX):
        lineage_id = lineage_id.removeprefix(_LEGACY_MARKER_PREFIX)
    _insert_marker(bind, _marker_key(lineage_id, api_key_scope))
    if legacy:
        _insert_marker(bind, _legacy_marker_key(lineage_id))


def _backfill_detached_markers(bind: Connection) -> None:
    sticky_columns = _columns(bind, "sticky_sessions")
    required_sticky = {"key", "kind", "account_id", "requires_security_work_authorized"}
    if required_sticky.issubset(sticky_columns):
        rows = bind.execute(
            sa.text(
                """
                SELECT key FROM sticky_sessions
                WHERE kind = :kind AND account_id IS NOT NULL AND requires_security_work_authorized = :required
                """
            ),
            {"kind": _CODEX_SESSION_KIND, "required": True},
        ).fetchall()
        for (lineage_id,) in rows:
            if isinstance(lineage_id, str):
                _backfill_marker(bind, lineage_id, None, legacy=True)

    bridge_columns = _columns(bind, "http_bridge_sessions")
    required_bridge = {"session_key_kind", "session_key_value", "api_key_scope", "requires_security_work_authorized"}
    if not required_bridge.issubset(bridge_columns):
        return
    turn_state = "latest_turn_state" if "latest_turn_state" in bridge_columns else "NULL"
    rows = bind.execute(
        sa.text(
            f"""
            SELECT session_key_kind, session_key_value, api_key_scope, {turn_state} AS latest_turn_state
            FROM http_bridge_sessions
            WHERE requires_security_work_authorized = :required
            """
        ),
        {"required": True},
    ).fetchall()
    for kind, value, scope, latest_turn_state in rows:
        if kind in _LINEAGE_ALIAS_KINDS and isinstance(value, str):
            _backfill_marker(bind, value, scope if isinstance(scope, str) else None)
        if isinstance(latest_turn_state, str):
            _backfill_marker(bind, latest_turn_state, scope if isinstance(scope, str) else None)

    alias_columns = _columns(bind, "http_bridge_session_aliases")
    required_alias = {"session_id", "alias_kind", "alias_value"}
    if not required_alias.issubset(alias_columns) or "id" not in bridge_columns:
        return
    if "api_key_scope" in alias_columns:
        alias_scope = "COALESCE(a.api_key_scope, s.api_key_scope, :anonymous_scope)"
    else:
        alias_scope = "COALESCE(s.api_key_scope, :anonymous_scope)"
    alias_rows = bind.execute(
        sa.text(
            f"""
            SELECT a.alias_value, {alias_scope} AS api_key_scope
            FROM http_bridge_session_aliases AS a
            JOIN http_bridge_sessions AS s ON s.id = a.session_id
            WHERE s.requires_security_work_authorized = :required
              AND a.alias_kind IN :alias_kinds
            """
        ).bindparams(sa.bindparam("alias_kinds", expanding=True)),
        {
            "required": True,
            "alias_kinds": list(_LINEAGE_ALIAS_KINDS),
            "anonymous_scope": _ANONYMOUS_SCOPE,
        },
    ).fetchall()
    for alias_value, scope in alias_rows:
        if isinstance(alias_value, str):
            _backfill_marker(bind, alias_value, scope if isinstance(scope, str) else None)


def _add_columns(bind: Connection) -> None:
    usage = _columns(bind, "usage_history")
    if usage:
        with op.batch_alter_table("usage_history") as batch:
            if "requires_security_work_authorized" not in usage:
                batch.add_column(
                    sa.Column(
                        "requires_security_work_authorized", sa.Boolean(), nullable=False, server_default=sa.false()
                    )
                )
            if not bool(usage.get("account_id", {}).get("nullable", False)):
                batch.alter_column("account_id", existing_type=sa.String(), nullable=True)
        if bind.dialect.name == "sqlite":
            # SQLite batch-alter rebuilds the table and does not preserve its
            # expression indexes, which are required by the usage hot path.
            op.execute(sa.text("DROP INDEX IF EXISTS idx_usage_window_account_latest"))
            op.execute(sa.text("DROP INDEX IF EXISTS idx_usage_window_account_time"))
            op.execute(
                sa.text(
                    "CREATE INDEX idx_usage_window_account_latest "
                    "ON usage_history (coalesce(\"window\", 'primary'), account_id, recorded_at DESC, id DESC)"
                )
            )
            op.execute(
                sa.text(
                    "CREATE INDEX idx_usage_window_account_time "
                    "ON usage_history (coalesce(\"window\", 'primary'), account_id, recorded_at DESC)"
                )
            )

    sticky = _columns(bind, "sticky_sessions")
    if sticky:
        account_foreign_key = _account_foreign_key(bind, "sticky_sessions")
        raw_account_foreign_key_options = account_foreign_key.get("options") if account_foreign_key else None
        account_foreign_key_options = (
            raw_account_foreign_key_options if isinstance(raw_account_foreign_key_options, Mapping) else {}
        )
        replace_account_foreign_key = (
            account_foreign_key is None or str(account_foreign_key_options.get("ondelete", "")).upper() != "SET NULL"
        )
        with op.batch_alter_table(
            "sticky_sessions",
            naming_convention=_BATCH_NAMING_CONVENTION,
        ) as batch:
            if "requires_security_work_authorized" not in sticky:
                batch.add_column(
                    sa.Column(
                        "requires_security_work_authorized", sa.Boolean(), nullable=False, server_default=sa.false()
                    )
                )
            if not bool(sticky.get("account_id", {}).get("nullable", False)):
                batch.alter_column("account_id", existing_type=sa.String(), nullable=True)
            if replace_account_foreign_key:
                if account_foreign_key is not None:
                    constraint_name = str(account_foreign_key.get("name") or "fk_sticky_sessions_account_id_accounts")
                    batch.drop_constraint(constraint_name, type_="foreignkey")
                batch.create_foreign_key(
                    "fk_sticky_sessions_account_id_accounts",
                    "accounts",
                    ["account_id"],
                    ["id"],
                    ondelete="SET NULL",
                )

    bridge = _columns(bind, "http_bridge_sessions")
    if bridge:
        with op.batch_alter_table("http_bridge_sessions") as batch:
            if "requires_security_work_authorized" not in bridge:
                batch.add_column(
                    sa.Column(
                        "requires_security_work_authorized", sa.Boolean(), nullable=False, server_default=sa.false()
                    )
                )
            if "latest_pending_function_call_ids" not in bridge:
                batch.add_column(sa.Column("latest_pending_function_call_ids", sa.Text(), nullable=True))
            if "latest_pending_custom_tool_call_ids" not in bridge:
                batch.add_column(sa.Column("latest_pending_custom_tool_call_ids", sa.Text(), nullable=True))

    quota = _columns(bind, "quota_planner_settings")
    if quota:
        with op.batch_alter_table("quota_planner_settings") as batch:
            if "auto_redeem_expiring_reset_credits" not in quota:
                batch.add_column(
                    sa.Column(
                        "auto_redeem_expiring_reset_credits", sa.Boolean(), nullable=False, server_default=sa.false()
                    )
                )
            if "reset_credit_redeem_lead_minutes" not in quota:
                batch.add_column(
                    sa.Column("reset_credit_redeem_lead_minutes", sa.Integer(), nullable=False, server_default="30")
                )


def upgrade() -> None:
    bind = op.get_bind()
    _add_columns(bind)
    _backfill_detached_markers(bind)


def downgrade() -> None:
    # This revision reconciles columns and detached markers that may have been
    # created by a previous aggregate. Their original owner cannot be inferred,
    # so dropping them could destroy live lineage data.
    return
