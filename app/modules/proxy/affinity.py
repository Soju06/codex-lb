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
from dataclasses import dataclass
from hashlib import sha256
from typing import cast
from uuid import uuid4

from app.core.config.settings import get_settings
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.db.models import StickySessionKind
from app.modules.api_keys.service import ApiKeyData


@dataclass(frozen=True, slots=True)
class _AffinityPolicy:
    key: str | None = None
    kind: StickySessionKind | None = None
    reallocate_sticky: bool = False
    max_age_seconds: int | None = None


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
    for key in ("session_id", "x-codex-session-id", "x-codex-conversation-id"):
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
        )
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
            )
    return _AffinityPolicy()


def _owner_lookup_session_id_from_headers(headers: Mapping[str, str]) -> str | None:
    # `x-codex-turn-state` is per conversation turn/thread and is more specific
    # than `session_id`, which may be shared across multiple terminals.
    turn_state = _sticky_key_from_turn_state_header(headers)
    if turn_state is not None:
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
    if turn_state_key:
        return _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
        )
    if codex_session_affinity:
        session_key = _sticky_key_from_session_header(headers)
        if session_key:
            return _AffinityPolicy(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
            )
    if openai_cache_affinity:
        return _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    if sticky_threads_enabled:
        return _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
        )
    return _AffinityPolicy()
