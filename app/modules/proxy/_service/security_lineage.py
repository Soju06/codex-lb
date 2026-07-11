from __future__ import annotations

import hashlib
from typing import Any

from app.db.models import StickySession, StickySessionKind
from app.modules.proxy.load_balancer import AccountSelection

_SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE = "security_work_authorization_required"
_SECURITY_WORK_UPSTREAM_POLICY_CODE = "cyber_policy"
_NO_SECURITY_WORK_AUTHORIZED_ACCOUNTS_CODE = "no_security_work_authorized_accounts"
_SECURITY_WORK_AUTHORIZATION_REQUIRED_HINT_GROUPS = (
    (
        "flagged for possible cybersecurity risk",
        "authorized for security work",
        "chatgpt.com/cyber",
    ),
    (
        "we take extra caution with cybersecurity requests",
        "trusted access",
    ),
    (
        "this content can't be shown",
        "enterprise-trusted-access-for-cyber",
    ),
)
_SECURITY_WORK_RETRY_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work. "
    "codex-lb is retrying on an account marked as authorized for security work."
)
_SECURITY_WORK_NO_AUTHORIZED_ACCOUNTS_MESSAGE = (
    "Upstream flagged this request as possible cybersecurity work, but no account is marked as authorized for "
    "security work. Mark an account with Trusted Access for Cyber as security-work-authorized before retrying "
    "security-classified sessions."
)


_SECURITY_LINEAGE_MARKER_PREFIX = "@security-work/v2/"
_LEGACY_SECURITY_LINEAGE_MARKER_PREFIX = "security-work:"


def _security_lineage_marker_key(security_lineage_id: str) -> str:
    digest = hashlib.sha256(security_lineage_id.encode("utf-8")).hexdigest()
    return f"{_SECURITY_LINEAGE_MARKER_PREFIX}{digest}"


def _legacy_security_lineage_marker_key(security_lineage_id: str) -> str:
    return f"{_LEGACY_SECURITY_LINEAGE_MARKER_PREFIX}{security_lineage_id}"


def _security_lineage_id_uses_reserved_namespace(security_lineage_id: str) -> bool:
    return security_lineage_id.startswith((_SECURITY_LINEAGE_MARKER_PREFIX, _LEGACY_SECURITY_LINEAGE_MARKER_PREFIX))


def _is_security_work_authorization_required_error(code: str | None, message: str | None) -> bool:
    normalized_code = (code or "").strip().lower()
    if normalized_code in {
        _SECURITY_WORK_AUTHORIZATION_REQUIRED_CODE,
        _SECURITY_WORK_UPSTREAM_POLICY_CODE,
    }:
        return True
    normalized_message = (message or "").strip().lower()
    if not normalized_message:
        return False
    return any(
        all(hint in normalized_message for hint in hint_group)
        for hint_group in _SECURITY_WORK_AUTHORIZATION_REQUIRED_HINT_GROUPS
    )


class _SecurityLineageMixin:
    async def _security_lineage_requires_security_work_authorized(
        self: Any,
        security_lineage_id: str | None,
    ) -> bool:
        if not security_lineage_id or not callable(self._repo_factory):
            return False
        async with self._repo_factory() as repos:
            sticky_sessions = getattr(repos, "sticky_sessions", None)
            if sticky_sessions is None:
                return False
            marker_entry = await sticky_sessions.get_entry(
                _security_lineage_marker_key(security_lineage_id),
                kind=StickySessionKind.CODEX_SESSION,
            )
            if isinstance(marker_entry, StickySession) and marker_entry.requires_security_work_authorized:
                return True
            legacy_marker_entry = await sticky_sessions.get_entry(
                _legacy_security_lineage_marker_key(security_lineage_id),
                kind=StickySessionKind.CODEX_SESSION,
            )
            if isinstance(legacy_marker_entry, StickySession) and legacy_marker_entry.requires_security_work_authorized:
                return True
            if _security_lineage_id_uses_reserved_namespace(security_lineage_id):
                return False
            entry = await sticky_sessions.get_entry(
                security_lineage_id,
                kind=StickySessionKind.CODEX_SESSION,
            )
            # Read ORM state before the repository context rolls back its
            # read-only transaction and detaches/expires the loaded row.
            # Test doubles commonly expose an unconfigured AsyncMock here; the
            # persisted repository always returns a real ORM row or None.
            return isinstance(entry, StickySession) and bool(entry.requires_security_work_authorized)

    async def _mark_security_lineage_requirement(
        self: Any,
        security_lineage_id: str | None,
        *,
        account_id: str,
    ) -> None:
        if not security_lineage_id:
            return
        async with self._repo_factory() as repos:
            await repos.sticky_sessions.upsert(
                _security_lineage_marker_key(security_lineage_id),
                None,
                kind=StickySessionKind.CODEX_SESSION,
                requires_security_work_authorized=True,
            )
            if not _security_lineage_id_uses_reserved_namespace(security_lineage_id):
                await repos.sticky_sessions.upsert(
                    security_lineage_id,
                    account_id,
                    kind=StickySessionKind.CODEX_SESSION,
                    requires_security_work_authorized=True,
                )

    async def _bind_security_lineage_selection(
        self: Any,
        security_lineage_id: str | None,
        selection: AccountSelection,
        *,
        require_security_work_authorized: bool,
    ) -> AccountSelection:
        selection.requires_security_work_authorized = require_security_work_authorized
        if (
            require_security_work_authorized
            and selection.account is not None
            and selection.account.security_work_authorized
        ):
            try:
                await self._mark_security_lineage_requirement(
                    security_lineage_id,
                    account_id=selection.account.id,
                )
            except BaseException:
                load_balancer = getattr(self, "_load_balancer", None)
                if load_balancer is not None:
                    await load_balancer.release_account_lease(selection.lease)
                raise
        return selection
