"""Shared account-owner consistency checks for proxy continuity sources."""

from __future__ import annotations

from collections.abc import Mapping

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error

HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND = "internal_unanchored_parallel"
HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX = "account-neutral-replay:v1:"
HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS = frozenset({"prompt_cache", "session_header", "turn_state_header"})
CONTINUITY_RESET_REQUIRED_CODE = "continuity_reset_required"
CONTINUITY_RESET_REQUIRED_MESSAGE = (
    "The previous upstream conversation is no longer available after the account-pool change. "
    "Start a new Codex conversation with /new, then retry."
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


def api_key_assignment_cutover_active(api_key: object | None) -> bool:
    """Return whether this key has crossed at least one assignment generation."""

    if api_key is None:
        return False
    try:
        generation = int(getattr(api_key, "account_assignment_generation", 1))
    except (TypeError, ValueError):
        return False
    return generation > 1


def api_key_assignment_excludes_owner(
    api_key: object | None,
    *,
    owner_account_id: str | None,
) -> bool:
    """Return whether a changed explicit assignment set proves owner removal."""

    if not owner_account_id or not api_key_assignment_cutover_active(api_key):
        return False
    if not bool(getattr(api_key, "account_assignment_scope_enabled", False)):
        return False
    assigned_account_ids = getattr(api_key, "assigned_account_ids", None)
    if not isinstance(assigned_account_ids, (list, tuple, set, frozenset)):
        return False
    return owner_account_id not in assigned_account_ids


def continuity_owner_unavailable_fields(
    api_key: object | None,
    *,
    owner_account_id: str | None = None,
    fallback_code: str = "previous_response_owner_unavailable",
    fallback_message: str = "Previous response owner account is unavailable; retry later.",
) -> tuple[str, str]:
    if api_key_assignment_excludes_owner(api_key, owner_account_id=owner_account_id):
        return CONTINUITY_RESET_REQUIRED_CODE, CONTINUITY_RESET_REQUIRED_MESSAGE
    return fallback_code, fallback_message


def continuity_error_type_and_param(error_code: str) -> tuple[str, str | None]:
    if error_code == CONTINUITY_RESET_REQUIRED_CODE:
        return "invalid_request_error", "previous_response_id"
    return "server_error", None


def _continuity_proxy_error(error_code: str, message: str) -> ProxyResponseError:
    error_type, error_param = continuity_error_type_and_param(error_code)
    payload = openai_error(
        error_code,
        message,
        error_type=error_type,
    )
    if error_param is not None:
        payload["error"]["param"] = error_param
    return ProxyResponseError(
        400 if error_code == CONTINUITY_RESET_REQUIRED_CODE else 502,
        payload,
    )


def continuity_owner_unavailable_error(
    api_key: object | None,
    *,
    owner_account_id: str | None = None,
) -> ProxyResponseError:
    error_code, message = continuity_owner_unavailable_fields(
        api_key,
        owner_account_id=owner_account_id,
    )
    return _continuity_proxy_error(error_code, message)


def continuity_reset_required_error() -> ProxyResponseError:
    return _continuity_proxy_error(
        CONTINUITY_RESET_REQUIRED_CODE,
        CONTINUITY_RESET_REQUIRED_MESSAGE,
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
