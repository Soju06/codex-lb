"""Sticky-affinity and prompt-cache key helpers for proxy routing.

This module owns the pure request/header policy used by ``ProxyService`` to
choose a sticky session family. Keeping it outside ``service.py`` makes the
routing decisions testable without adding more responsibility to the proxy
orchestration class.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal, cast
from uuid import uuid4

from app.core.config.settings import get_settings
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest, extract_input_file_ids
from app.db.models import StickySessionKind
from app.modules.api_keys.service import ApiKeyData

# This typed provenance is a routing capability: callers must never recover it
# from key text, because a client-controlled turn state can mimic any prefix.
_CodexSessionSource = Literal["session_header", "turn_state"]
# Request headers are stripped and HTTP forbids CR/LF, while PostgreSQL/SQLite
# text keys can safely retain LF. This sentinel makes the internal namespace
# structurally unreachable by every legacy raw header, even if its digest is
# disclosed.
_CODEX_SELECTION_KEY_PREFIX = "\ncodex-lb-affinity-v1"


@dataclass(frozen=True, slots=True)
class _AffinityPolicy:
    key: str | None = None
    kind: StickySessionKind | None = None
    reallocate_sticky: bool = False
    # Source capability only. Shared selection still revokes spillover for a
    # required owner or any stage that may carry account-local state.
    spill_on_account_cap: bool = False
    max_age_seconds: int | None = None
    codex_session_source: _CodexSessionSource | None = None
    # ``conversation`` has no dedicated owner index. Preserve that provenance
    # until selection can prove one hard owner or a one-account pool.
    require_unambiguous_account: bool = False

    @property
    def selection_key(self) -> str | None:
        if self.key is None or self.codex_session_source != "session_header":
            return self.key
        # CODEX_SESSION historically mixed raw session and turn-state values.
        # Namespace only the newly soft source; raw legacy rows stay hard so a
        # rolling upgrade cannot reinterpret existing continuity ownership.
        return _codex_session_selection_key(self.key)

    @property
    def legacy_selection_key(self) -> str | None:
        # Old replicas persisted bare session headers as raw CODEX_SESSION
        # keys. Always consult this alongside the soft row: any raw hit may be
        # hard turn-state ownership and therefore takes precedence.
        return self.key if self.codex_session_source == "session_header" else None

    @staticmethod
    def cap_spillover_allowed(
        capability: bool,
        preferred_account_id: str | None,
        request_stage: str,
    ) -> bool:
        """Keep soft cap spillover strictly before account-owned transport state."""
        return capability and preferred_account_id is None and request_stage in ("first_turn", "follow_up")

    @staticmethod
    def preferred_owner_sticky_inputs(
        sticky_key: str | None,
        sticky_kind: StickySessionKind | None,
        reallocate_sticky: bool,
        sticky_max_age_seconds: int | None,
        sticky_source: _CodexSessionSource | None,
        legacy_sticky_key: str | None,
    ) -> tuple[
        str | None,
        StickySessionKind | None,
        bool,
        int | None,
        _CodexSessionSource | None,
        str | None,
    ]:
        if sticky_source != "session_header":
            return (
                sticky_key,
                sticky_kind,
                reallocate_sticky,
                sticky_max_age_seconds,
                sticky_source,
                legacy_sticky_key,
            )
        # A resolved response/file/bridge owner bypasses the new soft row, but
        # the raw compatibility row still has to be checked for conflicting
        # legacy hard ownership. Selection receives no writable sticky key, so
        # a raw miss cannot manufacture or rebind a mapping.
        return None, StickySessionKind.CODEX_SESSION, False, sticky_max_age_seconds, sticky_source, legacy_sticky_key


def _codex_session_selection_key(key: str) -> str:
    # The digest avoids storing client values, while the header-impossible
    # sentinel above—not secrecy—provides source separation from raw rows.
    digest = sha256(key.encode()).hexdigest()
    return f"{_CODEX_SELECTION_KEY_PREFIX}:session_header:{digest}"


def _prompt_cache_key_from_request_model(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    typed_value = getattr(payload, "prompt_cache_key", None)
    if isinstance(typed_value, str) and typed_value:
        return typed_value
    if not payload.model_extra:
        return None
    extra_value = payload.model_extra.get("prompt_cache_key")
    if isinstance(extra_value, str) and extra_value:
        return extra_value
    camel_value = payload.model_extra.get("promptCacheKey")
    if isinstance(camel_value, str) and camel_value:
        return camel_value
    return None


def _extract_model_class(model: str) -> str:
    """Extract model class from model name for cache key prefix.

    Classification:
    - "mini" for gpt-5.4-mini
    - "codex" for gpt-5.3-codex* (any variant)
    - "std" for all others
    """
    if "codex" in model:
        return "codex"
    if "mini" in model:
        return "mini"
    return "std"


def _derive_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
) -> str:
    """Derive a stable, session-scoped prompt_cache_key when the client does not provide one.

    The generated key is scoped to (model-class, api-key, instructions-prefix,
    instruction-role input, first-user-input) so that:
    - Different model classes get *different* keys (prevents cache pollution).
    - Parallel sessions from the same API key get *different* keys (different first input).
    - Successive turns within one session get the *same* key (first input stays constant).
    - Different API keys never collide.
    """
    parts: list[str] = []
    model = getattr(payload, "model", None)
    model_class = _extract_model_class(model) if isinstance(model, str) and model else None

    if api_key is not None:
        parts.append(api_key.id[:12])

    instructions = getattr(payload, "instructions", None)
    if isinstance(instructions, str) and instructions:
        parts.append(sha256(instructions[:512].encode()).hexdigest()[:12])

    instruction_input_text = _extract_instruction_input(payload)
    if instruction_input_text:
        parts.append(sha256(instruction_input_text[:512].encode()).hexdigest()[:12])

    first_user_text = _extract_first_user_input(payload)
    if first_user_text:
        parts.append(sha256(first_user_text[:512].encode()).hexdigest()[:12])

    if not parts:
        random_suffix = uuid4().hex[:24]
        return f"{model_class}-{random_suffix}" if model_class is not None else random_suffix

    return "-".join([model_class, *parts]) if model_class is not None else "-".join(parts)


def _extract_instruction_input(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    input_value = getattr(payload, "input", None)
    if not isinstance(input_value, list):
        return None
    parts: list[str] = []
    for item in input_value:
        if not isinstance(item, dict):
            continue
        if item.get("role") not in ("system", "developer"):
            continue
        content_text = _extract_message_content_text(item.get("content"))
        if content_text:
            parts.append(content_text)
        else:
            parts.append(json.dumps(item, sort_keys=True, ensure_ascii=False))
        if sum(len(part) for part in parts) >= 512:
            break
    if not parts:
        return None
    return "\n".join(parts)[:512]


def _extract_first_user_input(payload: ResponsesRequest | ResponsesCompactRequest) -> str | None:
    """Return a text representation of the first user input item for cache key derivation."""
    input_value = getattr(payload, "input", None)
    if isinstance(input_value, str):
        return input_value[:512]
    if not isinstance(input_value, list):
        return None
    for item in input_value:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role == "user":
            content = item.get("content")
            content_text = _extract_message_content_text(content)
            if content_text:
                return content_text[:512]
            return json.dumps(item, sort_keys=True, ensure_ascii=False)[:512]
    return None


def _extract_message_content_text(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content_mapping = cast(Mapping[str, object], content)
        text = content_mapping.get("text")
        return text if isinstance(text, str) else None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        part_mapping = cast(Mapping[str, object], part)
        text = part_mapping.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts) if parts else None


def _sticky_key_from_payload(payload: ResponsesRequest) -> str | None:
    value = _prompt_cache_key_from_request_model(payload)
    if not value:
        return None
    stripped = value.strip()
    return stripped or None


def _sticky_key_from_session_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for key in ("session_id", "session-id", "x-codex-session-id", "x-codex-conversation-id", "thread-id"):
        value = normalized.get(key)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _sticky_key_from_turn_state_header(headers: Mapping[str, str]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    value = normalized.get("x-codex-turn-state")
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _bare_codex_session_affinity(
    headers: Mapping[str, str],
    *,
    enabled: bool,
    allow_cap_spillover: bool,
) -> _AffinityPolicy | None:
    if not enabled:
        return None
    session_key = _sticky_key_from_session_header(headers)
    if session_key is None:
        return None
    return _AffinityPolicy(
        key=session_key,
        kind=StickySessionKind.CODEX_SESSION,
        spill_on_account_cap=allow_cap_spillover,
        codex_session_source="session_header",
    )


def _request_allows_bare_session_cap_spillover(
    payload: ResponsesRequest | ResponsesCompactRequest,
) -> bool:
    if isinstance(payload, ResponsesRequest):
        previous_response_id = payload.previous_response_id
        conversation = payload.conversation
    else:
        extra = payload.model_extra or {}
        previous_response_id = extra.get("previous_response_id")
        conversation = extra.get("conversation")
    # A lookup miss does not make an upstream-stored object portable. Selection
    # must remain fail-closed for every owner-bearing payload shape.
    return not (
        (previous_response_id is not None and not isinstance(previous_response_id, str))
        or (isinstance(previous_response_id, str) and bool(previous_response_id.strip()))
        or (conversation is not None and not isinstance(conversation, str))
        or (isinstance(conversation, str) and bool(conversation.strip()))
        or extract_input_file_ids(payload.input)
    )


def _affinity_with_payload_continuity(
    policy: _AffinityPolicy,
    payload: ResponsesRequest | ResponsesCompactRequest,
) -> _AffinityPolicy:
    if isinstance(payload, ResponsesRequest):
        conversation = payload.conversation
    else:
        conversation = (payload.model_extra or {}).get("conversation")
    if conversation is None or (isinstance(conversation, str) and not conversation.strip()):
        return policy
    return replace(policy, require_unambiguous_account=True)


def _sticky_key_for_codex_control_request(
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
) -> _AffinityPolicy:
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key:
        return _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    session_affinity = _bare_codex_session_affinity(
        headers,
        enabled=codex_session_affinity,
        allow_cap_spillover=False,
    )
    if session_affinity is not None:
        return session_affinity
    return _AffinityPolicy()


def _owner_lookup_session_id_from_headers(
    headers: Mapping[str, str],
    *,
    synthesized_turn_state: str | None = None,
) -> str | None:
    # `x-codex-turn-state` is per conversation turn/thread and is more specific
    # than `session_id`, which may be shared across multiple terminals. A turn
    # state generated for the current downstream connection is only an
    # upstream-forwarding placeholder, however; it must not hide a durable
    # client session on reconnect.
    turn_state = _sticky_key_from_turn_state_header(headers)
    if turn_state is not None and turn_state != synthesized_turn_state:
        return turn_state
    return _sticky_key_from_session_header(headers)


# Pattern matching turn-state values synthesized by the helpers below.
# A 32-char lowercase hex (uuid4().hex) suffix follows the prefix.
_SYNTHESIZED_TURN_STATE_PATTERN = re.compile(r"^(?:http_)?turn_[0-9a-f]{32}$")


def _is_synthesized_turn_state(value: str) -> bool:
    """True when ``value`` matches a turn-state synthesized by codex-lb itself.

    Used by the file-pin resolver to distinguish a client-supplied
    continuation marker from a synthesizer-generated placeholder so
    first-turn upload-then-converse requests still benefit from
    file_id pin routing on the websocket / HTTP entry points.
    """
    return bool(_SYNTHESIZED_TURN_STATE_PATTERN.match(value))


def ensure_downstream_turn_state(headers: Mapping[str, str]) -> str:
    existing = _sticky_key_from_turn_state_header(headers)
    if existing is not None:
        return existing
    return f"turn_{uuid4().hex}"


def ensure_http_downstream_turn_state(headers: Mapping[str, str]) -> str:
    existing = _sticky_key_from_turn_state_header(headers)
    if existing is not None:
        return existing
    return f"http_turn_{uuid4().hex}"


def build_downstream_turn_state_accept_headers(turn_state: str) -> list[tuple[bytes, bytes]]:
    return [(b"x-codex-turn-state", turn_state.encode("utf-8"))]


def build_downstream_turn_state_response_headers(turn_state: str) -> dict[str, str]:
    return {"x-codex-turn-state": turn_state}


def _resolve_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    *,
    openai_cache_affinity: bool,
    api_key: ApiKeyData | None,
) -> tuple[str | None, str]:
    cache_key = _prompt_cache_key_from_request_model(payload)
    if isinstance(cache_key, str):
        stripped = cache_key.strip()
        if stripped:
            if stripped != cache_key:
                payload.prompt_cache_key = stripped
            return stripped, "payload"
    if not openai_cache_affinity:
        return None, "none"
    settings = get_settings()
    if not settings.openai_prompt_cache_key_derivation_enabled:
        return None, "none"
    cache_key = _derive_prompt_cache_key(payload, api_key)
    payload.prompt_cache_key = cache_key
    return cache_key, "derived"


def _sticky_key_for_responses_request(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    *,
    codex_session_affinity: bool,
    openai_cache_affinity: bool,
    openai_cache_affinity_max_age_seconds: int,
    sticky_threads_enabled: bool,
    api_key: ApiKeyData | None = None,
    synthesized_turn_state: str | None = None,
) -> _AffinityPolicy:
    # This helper only classifies locality keys. Stored-object continuity such
    # as `previous_response_id` is resolved later by ProxyService and must stay
    # hard owner-bound even if this returns a prompt-cache affinity policy.
    cache_key, _ = _resolve_prompt_cache_key(
        payload,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
    )
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key and turn_state_key != synthesized_turn_state:
        policy = _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    elif (
        session_affinity := _bare_codex_session_affinity(
            headers,
            enabled=codex_session_affinity,
            allow_cap_spillover=_request_allows_bare_session_cap_spillover(payload),
        )
    ) is not None:
        policy = session_affinity
    elif openai_cache_affinity:
        policy = _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    elif sticky_threads_enabled:
        policy = _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    elif turn_state_key is not None and turn_state_key == synthesized_turn_state:
        policy = _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    else:
        policy = _AffinityPolicy()
    return _affinity_with_payload_continuity(policy, payload)
