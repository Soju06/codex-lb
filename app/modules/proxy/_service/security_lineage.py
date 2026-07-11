from __future__ import annotations

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
        if not security_lineage_id:
            return False
        async with self._repo_factory() as repos:
            sticky_sessions = getattr(repos, "sticky_sessions", None)
            if sticky_sessions is None:
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
        if (
            require_security_work_authorized
            and selection.account is not None
            and selection.account.security_work_authorized
        ):
            await self._mark_security_lineage_requirement(
                security_lineage_id,
                account_id=selection.account.id,
            )
        return selection
