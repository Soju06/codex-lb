"""Shared account-owner consistency checks for proxy continuity sources."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error

HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND = "internal_unanchored_parallel"
HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX = "account-neutral-replay:v1:"
HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS = frozenset({"prompt_cache", "session_header", "turn_state_header"})
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
        raise ProxyResponseError(
            502,
            openai_error(
                "continuity_owner_conflict",
                f"Account-owned continuity sources conflict ({sources}); retry the logical turn.",
                error_type="server_error",
            ),
        )
    return owner_account_id
