"""Shared account-owner consistency checks for proxy continuity sources."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from hashlib import sha256
from typing import NamedTuple, Protocol

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error

HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND = "internal_unanchored_parallel"
HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX = "account-neutral-replay:v1:"
# These are canonical lanes whose exact aliases may move only after the
# existing full-resend validator has proved the request account-neutral. A
# thread lane is hard during ordinary use, just like a session-header lane;
# omitting it here would accidentally remove safe owner-unavailable recovery
# merely because Codex now supplies a more precise canonical identity.
HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS = frozenset(
    {"prompt_cache", "session_header", "thread_header", "turn_state_header"}
)
_HTTP_BRIDGE_SESSION_AFFINITY_HEADERS = frozenset(
    {
        "session_id",
        "session-id",
        "thread-id",
        "x-codex-conversation-id",
        "x-codex-session-id",
        "x-codex-turn-state",
    }
)
logger = logging.getLogger("app.modules.proxy.continuity")


def make_http_bridge_account_neutral_replay_key(nonce: str) -> tuple[str, str]:
    if not nonce:
        raise ValueError("account-neutral replay nonce must not be empty")
    return (
        HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
        f"{HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX}{nonce}",
    )


def is_http_bridge_account_neutral_replay(*, kind: str, key: str) -> bool:
    """Recognize only server-namespaced durable replay keys."""

    return (
        kind == HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND
        and key.startswith(HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX)
        and len(key) > len(HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX)
    )


def without_http_bridge_session_affinity_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Drop downstream aliases that must not reach a fresh upstream account."""

    return {
        header_name: header_value
        for header_name, header_value in headers.items()
        if header_name.lower() not in _HTTP_BRIDGE_SESSION_AFFINITY_HEADERS
    }


class _ReconnectPreferredOwner(Protocol):
    preferred_account_id: str | None
    file_required_preferred_account: bool


class _AccountNeutralReplayKey(Protocol):
    # Read-only members: session keys are frozen dataclasses, and a plain
    # attribute declaration would demand a writable one.
    @property
    def affinity_kind(self) -> str: ...

    @property
    def affinity_key(self) -> str: ...


class AccountNeutralOwnerBinding(NamedTuple):
    """How a reconnect must treat the account that opened an account-neutral replay."""

    owner_bound: bool
    rebind_allowed: bool


def resolve_account_neutral_owner_binding(
    key: _AccountNeutralReplayKey,
    request_state: _ReconnectPreferredOwner,
    allow_account_rebind: bool,
) -> AccountNeutralOwnerBinding:
    """Decide whether an account-neutral replay still binds its opening account.

    A server-namespaced replay key can be continued by any account, but most
    reconnects also resume account-scoped upstream state, so the opening account
    stays bound by default. Only a caller that has proven its request carries no
    such state -- no continuity anchor, no account-scoped file, nothing streamed
    yet -- passes ``allow_account_rebind``, and only then may the reconnect land
    on a replacement account. Deriving that here from the key alone would
    override the caller, which is how a fresh replay stayed pinned to an account
    that had gone silent.

    ``owner_bound`` and ``rebind_allowed`` are not opposites: a key that was
    never account-neutral is neither, so a caller that allows a rebind cannot
    loosen owner requirements on an ordinary account-bound session.
    """

    if not is_http_bridge_account_neutral_replay(kind=key.affinity_kind, key=key.affinity_key):
        return AccountNeutralOwnerBinding(owner_bound=False, rebind_allowed=False)
    if allow_account_rebind and not request_state.file_required_preferred_account:
        return AccountNeutralOwnerBinding(owner_bound=False, rebind_allowed=True)
    return AccountNeutralOwnerBinding(owner_bound=True, rebind_allowed=False)


def resolve_reconnect_preferred_account_id(
    request_state: _ReconnectPreferredOwner,
    session_account_id: str,
    require_preferred_account: bool,
    account_neutral_recovery: bool,
) -> str | None:
    if request_state.file_required_preferred_account:
        return request_state.preferred_account_id or session_account_id
    if require_preferred_account or account_neutral_recovery:
        return request_state.preferred_account_id
    return None


def resolve_required_account_id(*owners: tuple[str, str | None]) -> str | None:
    """Return one proven owner or fail closed when hard sources disagree."""
    resolved = [(source, account_id) for source, account_id in owners if account_id is not None]
    if not resolved:
        return None
    owner_account_id = resolved[0][1]
    conflicting_sources = [source for source, account_id in resolved if account_id != owner_account_id]
    if conflicting_sources:
        # Hard sources identify account-scoped upstream state. Choosing either
        # side would silently abandon the other, so conflicts are never ordered
        # by caller precedence or softened into ordinary affinity fallback.
        sources = ", ".join(source for source, _account_id in resolved)
        owner_hashes = ", ".join(
            f"{source}={sha256(account_id.encode()).hexdigest()[:12]}" for source, account_id in resolved
        )
        logger.warning(
            "continuity_owner_conflict sources=%s conflicting_sources=%s owner_hashes=%s",
            sources,
            ", ".join(conflicting_sources),
            owner_hashes,
        )
        raise ProxyResponseError(
            502,
            openai_error(
                "continuity_owner_conflict",
                f"Account-owned continuity sources conflict ({sources}); retry the logical turn.",
                error_type="server_error",
            ),
        )
    return owner_account_id
