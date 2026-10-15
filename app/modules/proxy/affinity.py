"""Sticky-affinity and prompt-cache key helpers for proxy routing.

This module owns the pure request/header policy used by ``ProxyService`` to
choose a sticky session family. Keeping it outside ``service.py`` makes the
routing decisions testable without adding more responsibility to the proxy
orchestration class.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal, TypedDict
from uuid import uuid4

from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    prompt_cache_key_derivation_total,
)
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
    extract_input_file_ids,
    responses_request_contains_goal_continuation_context,
)
from app.db.models import StickySessionKind
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay
from app.modules.proxy.thread_anchors import (
    build_thread_window,
    get_thread_anchor_index,
    thread_anchor_domain,
)

# This typed provenance is a routing capability: callers must never recover it
# from key text, because a client-controlled turn state can mimic any prefix.
_CodexSessionSource = Literal["session_header", "thread_header", "turn_state"]
# Request headers are stripped and HTTP forbids CR/LF, while PostgreSQL/SQLite
# text keys can safely retain LF. This sentinel makes the internal namespace
# structurally unreachable by every legacy raw header, even if its digest is
# disclosed.
_CODEX_SELECTION_KEY_PREFIX = "\ncodex-lb-affinity-v1"


class _AffinitySelectionKwargs(TypedDict):
    sticky_key: str | None
    sticky_kind: StickySessionKind | None
    reallocate_sticky: bool
    sticky_source: _CodexSessionSource | None
    legacy_sticky_key: str | None
    legacy_continuity_source: _CodexSessionSource | None
    sticky_seed_key: str | None
    sticky_seed_kind: StickySessionKind | None
    spill_bare_session_on_account_cap: bool
    abandon_unavailable_legacy_owner: bool
    require_unambiguous_account: bool
    sticky_max_age_seconds: int | None


@dataclass(frozen=True, slots=True)
class _AffinityPolicy:
    key: str | None = None
    kind: StickySessionKind | None = None
    reallocate_sticky: bool = False
    # Source capability only. Shared selection still revokes spillover for a
    # required owner or any stage that may carry account-local state.
    spill_on_account_cap: bool = False
    # An explicit, self-contained Codex goal restart may retire only a raw
    # compatibility owner whose durable account status is unavailable.
    abandon_unavailable_legacy_owner: bool = False
    max_age_seconds: int | None = None
    codex_session_source: _CodexSessionSource | None = None
    # A thread row is soft locality, but old replicas may have persisted the
    # raw process/session value as hard CODEX_SESSION ownership. Keep that
    # compatibility lookup explicit instead of trying to reconstruct it from
    # the new opaque thread key.
    legacy_codex_session_key: str | None = None
    # Interpretation used when consulting that raw key. Process-session text
    # is session_header even on a thread-scoped request; a thread-only raw
    # key stays thread_header so a session_header tombstone cannot hide it.
    legacy_continuity_source: _CodexSessionSource | None = None
    # A previously unseen thread should inherit the healthy process preference
    # once, then persist its own bounded row. This is never ownership: a
    # missing process default may be initialized once by insert-if-absent, but
    # no thread request may update or delete an established process row.
    seed_selection_key: str | None = None
    seed_selection_kind: StickySessionKind | None = None
    # ``conversation`` has no dedicated owner index. Preserve that provenance
    # until selection can prove one hard owner or a one-account pool.
    require_unambiguous_account: bool = False
    # For a policy whose key IS the prompt cache key: whether the client sent
    # that key (``payload``) or the proxy derived it (``derived``). Recorded by
    # the resolver rather than re-derived by callers, because
    # ``_resolve_prompt_cache_key`` writes the derived key back onto the
    # payload — so a later reader of the payload cannot tell the two apart, and
    # a blank client hint the resolver rejected still looks present. Routing
    # never reads this; it exists so the request log can describe the decision.
    prompt_cache_key_source: str | None = None
    # How the forwarded ``prompt_cache_key`` was obtained this turn. Diagnostic
    # only: it never participates in routing, but it is the signal that tells
    # an operator whether unanchored threads are being held or are churning.
    prompt_cache_derivation_outcome: str | None = None

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
        if self.legacy_codex_session_key is not None:
            return self.legacy_codex_session_key
        return self.key if self.codex_session_source == "session_header" else None

    def selection_kwargs(self) -> _AffinitySelectionKwargs:
        """Expand routing policy once at the account-selection boundary."""

        # Keep the compatibility edge from silently omitting new policy
        # fields. In particular, thread locality is incomplete if callers pass
        # its row but forget the process seed or legacy hard-owner lookup.
        return {
            "sticky_key": self.selection_key,
            "sticky_kind": self.kind,
            "reallocate_sticky": self.reallocate_sticky,
            "sticky_source": self.codex_session_source,
            "legacy_sticky_key": self.legacy_selection_key,
            "legacy_continuity_source": (
                None if self.legacy_selection_key is None else (self.legacy_continuity_source or "session_header")
            ),
            "sticky_seed_key": self.seed_selection_key,
            "sticky_seed_kind": self.seed_selection_kind,
            "spill_bare_session_on_account_cap": self.spill_on_account_cap,
            "abandon_unavailable_legacy_owner": self.abandon_unavailable_legacy_owner,
            "require_unambiguous_account": self.require_unambiguous_account,
            "sticky_max_age_seconds": self.max_age_seconds,
        }

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
        if sticky_source not in {"session_header", "thread_header"}:
            return (
                sticky_key,
                sticky_kind,
                reallocate_sticky,
                sticky_max_age_seconds,
                sticky_source,
                legacy_sticky_key,
            )
        # A resolved response/file/bridge owner bypasses the current-Codex
        # soft row (process-session or thread PROMPT_CACHE). The raw
        # compatibility row still has to be checked for conflicting legacy
        # hard ownership. Selection receives no writable sticky key, so a
        # raw miss cannot manufacture or rebind a mapping. The caller also
        # deliberately omits any broader process seed in this exact-owner path.
        return None, StickySessionKind.CODEX_SESSION, False, sticky_max_age_seconds, sticky_source, legacy_sticky_key


def _codex_session_selection_key(key: str) -> str:
    # The digest avoids storing client values, while the header-impossible
    # sentinel above—not secrecy—provides source separation from raw rows.
    digest = sha256(key.encode()).hexdigest()
    return f"{_CODEX_SELECTION_KEY_PREFIX}:session_header:{digest}"


@dataclass(frozen=True, slots=True)
class _CodexBackendIdentity:
    """Independently parsed process-tree and logical-thread identities."""

    process_session: str | None
    thread_id: str | None

    @property
    def thread_selection_key(self) -> str | None:
        if self.thread_id is None:
            return None
        # The explicit scope tag prevents the thread-only compatibility form
        # from colliding with (process, thread). Length framing keeps distinct
        # client tuples distinct even if a future non-HTTP caller admits NULs
        # or other delimiters. The LF namespace remains unreachable by headers.
        if self.process_session is None:
            parts = ("thread-only", self.thread_id)
            scope = "thread_only"
        else:
            parts = ("process-thread", self.process_session, self.thread_id)
            scope = "process_thread"
        encoded_parts = (part.encode() for part in parts)
        framed = b"".join(len(part).to_bytes(8, "big") + part for part in encoded_parts)
        digest = sha256(framed).hexdigest()
        return f"{_CODEX_SELECTION_KEY_PREFIX}:thread_header:{scope}:{digest}"


_CODEX_PROCESS_SESSION_HEADERS = (
    "session_id",
    "session-id",
    "x-codex-session-id",
    "x-codex-conversation-id",
)


def _normalized_header_value(headers: Mapping[str, str], names: tuple[str, ...]) -> str | None:
    normalized = {key.lower(): value for key, value in headers.items()}
    for name in names:
        value = normalized.get(name)
        if not isinstance(value, str):
            continue
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _process_session_key_from_headers(headers: Mapping[str, str]) -> str | None:
    return _normalized_header_value(headers, _CODEX_PROCESS_SESSION_HEADERS)


def _thread_id_from_headers(headers: Mapping[str, str]) -> str | None:
    return _normalized_header_value(headers, ("thread-id",))


def _codex_backend_identity(
    headers: Mapping[str, str],
    *,
    thread_id: str | None = None,
) -> _CodexBackendIdentity:
    normalized_thread_id = thread_id.strip() if isinstance(thread_id, str) and thread_id.strip() else None
    return _CodexBackendIdentity(
        process_session=_process_session_key_from_headers(headers),
        thread_id=normalized_thread_id if thread_id is not None else _thread_id_from_headers(headers),
    )


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


# Version marker for keys minted by the thread-anchor derivation. It also
# separates the new shape from the legacy content-hash shape
# (``{model_class}-{api_key_id[:12]}-{hash}...``) so the stale legacy rows can
# be swept by key prefix. The readable model class and API-key prefix are kept
# because operators search ``sticky_sessions`` by key text.
_ANCHORED_KEY_VERSION = "v2t"
# Fallback when no caller supplies the dashboard freshness window. Matches the
# ``openai_cache_affinity_max_age_seconds`` column default.
_DEFAULT_ANCHOR_TTL_SECONDS = 1800

DERIVATION_OUTCOME_PAYLOAD = "payload"
DERIVATION_OUTCOME_DISABLED = "disabled"
DERIVATION_OUTCOME_ANCHOR_HIT = "anchor_hit"
DERIVATION_OUTCOME_ANCHOR_NEW = "anchor_new"
DERIVATION_OUTCOME_ANCHOR_RESET = "anchor_reset"
DERIVATION_OUTCOME_UNANCHORABLE = "unanchorable"


@dataclass(frozen=True, slots=True)
class _PromptCacheAnchor:
    """Result of anchoring one turn.

    ``sticky_key`` is both the value forwarded upstream and the sticky routing
    key -- deliberately the same string, so there is no proxy-minted value that
    is forwarded but must not be routed on. It is ``None`` when the turn is
    unanchorable: there is no transcript to hold, so there is nothing to name,
    selection takes the unbound path, and **nothing is written onto the
    payload**.

    That last part is load-bearing. The same payload object is resolved a
    second time on the real bridge -> ``_stream_with_retry`` fallback, and a
    written placeholder comes back through the client-supplied branch: a
    constant ``<class>-<apikey12>`` string promoted to a PROMPT_CACHE sticky
    key, collapsing every unanchorable thread of one API key onto one row and
    one account, reported as ``source=payload``.
    """

    sticky_key: str | None
    outcome: str


def _input_contains_prior_assistant_turn(payload: ResponsesRequest | ResponsesCompactRequest) -> bool:
    """Whether this body already carries model output from an earlier turn."""

    input_value = getattr(payload, "input", None)
    if not isinstance(input_value, list):
        return False
    for item in input_value:
        if not isinstance(item, dict):
            continue
        if item.get("role") == "assistant" or item.get("type") == "reasoning":
            return True
    return False


def _derive_prompt_cache_anchor(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
    *,
    max_age_seconds: int = _DEFAULT_ANCHOR_TTL_SECONDS,
) -> _PromptCacheAnchor:
    """Anchor a turn with no client-supplied ``prompt_cache_key`` to its thread.

    The key is minted once per thread and then reused for every turn whose
    transcript verifiably extends the previous one (see
    ``thread_anchors``). It is therefore stable across appends, across a
    client trimming its leading history, and across an identical re-derivation
    of the same body (bridge-to-HTTP fallback, cross-transport replay). It
    deliberately does **not** survive compaction: a compacted turn does not
    extend the stored transcript, the upstream prefix cache is cold, and the
    only way to bridge it would be fuzzy matching that merges distinct threads.

    Unlike the previous content-hash derivation, nothing here is a truncated
    digest of client text, so two threads that share their first 512
    characters -- one `<environment_context>` block for one cwd, for
    ``codex_cli_rs`` -- no longer collapse onto one key.
    """

    model = getattr(payload, "model", None)
    model_class = _extract_model_class(model) if isinstance(model, str) and model else None
    api_key_id = api_key.id if api_key is not None else ""
    instructions = getattr(payload, "instructions", None)
    instructions_text = instructions if isinstance(instructions, str) else ""
    readable_parts = [part for part in (model_class, api_key_id[:12] or None) if part]

    window = build_thread_window(
        getattr(payload, "input", None),
        domain=thread_anchor_domain(
            owner_row_id=api_key_id,
            model_class=model_class or "",
            instructions=instructions_text,
        ),
    )
    if window is None:
        return _PromptCacheAnchor(sticky_key=None, outcome=DERIVATION_OUTCOME_UNANCHORABLE)

    index = get_thread_anchor_index()
    anchored_key = index.lookup(window, ttl_seconds=max_age_seconds)
    if anchored_key is not None:
        index.register(anchored_key, window)
        return _PromptCacheAnchor(anchored_key, DERIVATION_OUTCOME_ANCHOR_HIT)

    minted_key = "-".join([_ANCHORED_KEY_VERSION, *readable_parts, uuid4().hex[:16]])
    index.register(minted_key, window)
    # A body that already carries model output belonged to a thread we could
    # not match: compaction, a restart, or an anchor eviction. Separating it
    # from a genuinely first turn is what makes the counter actionable.
    outcome = (
        DERIVATION_OUTCOME_ANCHOR_RESET
        if _input_contains_prior_assistant_turn(payload)
        else DERIVATION_OUTCOME_ANCHOR_NEW
    )
    return _PromptCacheAnchor(minted_key, outcome)


def _derive_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    api_key: ApiKeyData | None,
    *,
    max_age_seconds: int = _DEFAULT_ANCHOR_TTL_SECONDS,
) -> str | None:
    """Key attached to the payload and forwarded upstream, or ``None``.

    ``None`` means the turn is unanchorable and nothing is attached; see
    ``_PromptCacheAnchor``.
    """

    return _derive_prompt_cache_anchor(payload, api_key, max_age_seconds=max_age_seconds).sticky_key


def _sticky_key_from_session_header(headers: Mapping[str, str]) -> str | None:
    # Legacy owner/request-log callers still need the historical alias order.
    # New account, bridge, and replay locality MUST use the typed process/thread
    # helpers above; otherwise a shared process id silently hides thread-id.
    return _process_session_key_from_headers(headers) or _thread_id_from_headers(headers)


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


def _thread_codex_session_affinity(
    headers: Mapping[str, str],
    *,
    enabled: bool,
    max_age_seconds: int,
    thread_id: str | None = None,
) -> _AffinityPolicy | None:
    if not enabled:
        return None
    identity = _codex_backend_identity(headers, thread_id=thread_id)
    thread_key = identity.thread_selection_key
    if thread_key is None:
        return None
    # Current Codex shares process session and prompt_cache_key across a root
    # tree. Thread locality therefore reuses the bounded PROMPT_CACHE lifecycle
    # but does not rewrite the upstream cache hint or create durable child rows.
    legacy_key = identity.process_session or identity.thread_id
    return _AffinityPolicy(
        key=thread_key,
        kind=StickySessionKind.PROMPT_CACHE,
        max_age_seconds=max_age_seconds,
        codex_session_source="thread_header",
        legacy_codex_session_key=legacy_key,
        legacy_continuity_source=("session_header" if identity.process_session is not None else "thread_header"),
        seed_selection_key=(
            _codex_session_selection_key(identity.process_session) if identity.process_session is not None else None
        ),
        seed_selection_kind=(StickySessionKind.CODEX_SESSION if identity.process_session is not None else None),
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


def _request_allows_unavailable_legacy_owner_abandonment(payload: ResponsesRequest) -> bool:
    # Intent and safety are separate proofs. The internal goal marker says the
    # client deliberately restarted, while the replay classifier proves that
    # this particular body carries no account-scoped state. Never collapse this
    # into a marker-only or missing-previous-response shortcut.
    if not responses_request_contains_goal_continuation_context(payload):
        return False
    # Classify the same canonical body that subscription egress would send.
    # Raw model dumps retain accepted compatibility-only controls, which are
    # not account state and must not make equivalent request forms disagree.
    replay_payload = dict(payload.to_replay_safety_payload())
    # ``type=response.create`` belongs to the direct-WebSocket envelope, not
    # the HTTP Responses body. Remove only that exact discriminator after
    # canonicalization; unknown envelope values remain fail-closed below.
    if replay_payload.get("type") == "response.create":
        replay_payload.pop("type")
    return responses_payload_is_account_neutral_fresh_replay(replay_payload)


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


def _sticky_key_for_thread_goal_request(
    payload: Mapping[str, object],
    headers: Mapping[str, str],
    codex_session_affinity: bool,
    max_age_seconds: int,
) -> _AffinityPolicy:
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key is not None:
        return _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    payload_thread_id = payload.get("threadId")
    if isinstance(payload_thread_id, str) and payload_thread_id.strip():
        thread_affinity = _thread_codex_session_affinity(
            headers,
            enabled=codex_session_affinity,
            max_age_seconds=max_age_seconds,
            thread_id=payload_thread_id,
        )
        if thread_affinity is not None:
            return thread_affinity
    # Routing only consumes a valid nonblank identity. The upstream thread-goal
    # protocol remains authoritative for payload validation and error shape.
    return _sticky_key_for_codex_control_request(
        headers,
        codex_session_affinity=codex_session_affinity,
    )


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


def _websocket_continuity_key_from_headers(
    headers: Mapping[str, str],
    *,
    synthesized_turn_state: str | None = None,
) -> str | None:
    """Return the primary count-bounded direct-WebSocket continuity key."""

    explicit_turn_state = _sticky_key_from_turn_state_header(headers)
    if explicit_turn_state is not None and explicit_turn_state != synthesized_turn_state:
        # Exact client continuation must outrank broader thread locality. A
        # synthesized handshake placeholder is only an alias for the current
        # connection and therefore does not gain this hard precedence.
        return explicit_turn_state
    identity = _codex_backend_identity(headers)
    if identity.thread_selection_key is not None:
        return identity.thread_selection_key
    return _owner_lookup_session_id_from_headers(
        headers,
        synthesized_turn_state=synthesized_turn_state,
    )


def _websocket_continuity_aliases_from_headers(
    headers: Mapping[str, str],
    *,
    synthesized_turn_state: str | None = None,
) -> tuple[str, ...]:
    """Keep exact turn aliases without restoring process-wide thread state."""

    aliases: list[str] = []
    primary = _websocket_continuity_key_from_headers(
        headers,
        synthesized_turn_state=synthesized_turn_state,
    )
    if primary is not None:
        aliases.append(primary)
    thread_key = _codex_backend_identity(headers).thread_selection_key
    if thread_key is not None:
        # When an exact turn resolved first, refresh the thread alias to that
        # same state so a later unanchored reconnect remains thread-local.
        aliases.append(thread_key)
    explicit_turn_state = _sticky_key_from_turn_state_header(headers)
    if explicit_turn_state is not None and explicit_turn_state != synthesized_turn_state:
        aliases.append(explicit_turn_state)
    if synthesized_turn_state is not None:
        aliases.append(synthesized_turn_state)
    return tuple(dict.fromkeys(aliases))


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


@dataclass(frozen=True, slots=True)
class _PromptCacheResolution:
    """Routing key plus the provenance operators need to read a shape log."""

    sticky_key: str | None
    source: str
    outcome: str


def _resolve_prompt_cache_key(
    payload: ResponsesRequest | ResponsesCompactRequest,
    *,
    openai_cache_affinity: bool,
    api_key: ApiKeyData | None,
    max_age_seconds: int = _DEFAULT_ANCHOR_TTL_SECONDS,
) -> _PromptCacheResolution:
    cache_key = _prompt_cache_key_from_request_model(payload)
    if isinstance(cache_key, str):
        stripped = cache_key.strip()
        if stripped:
            if stripped != cache_key:
                payload.prompt_cache_key = stripped
            return _record_prompt_cache_resolution(
                _PromptCacheResolution(stripped, "payload", DERIVATION_OUTCOME_PAYLOAD)
            )
    if not openai_cache_affinity:
        return _record_prompt_cache_resolution(_PromptCacheResolution(None, "none", DERIVATION_OUTCOME_DISABLED))
    anchor = _derive_prompt_cache_anchor(payload, api_key, max_age_seconds=max_age_seconds)
    if anchor.sticky_key is None:
        # Unanchorable: attach nothing. A value written here is indistinguishable
        # from a client-supplied one when this same payload object is resolved
        # again (bridge -> `_stream_with_retry` fallback), which would promote a
        # constant per-API-key string into a real sticky key. There is also
        # nothing for upstream to cache: the body carried no digestible
        # transcript. `http_continuation_signal` falls through to
        # `http_history_signal`, which still classifies a real transcript.
        return _record_prompt_cache_resolution(_PromptCacheResolution(None, "none", anchor.outcome))
    # Attached so that a re-resolution of this same body -- the bridge fallback,
    # or another replica after ring forwarding -- reuses the identical key
    # through the client-supplied branch instead of re-deriving it.
    payload.prompt_cache_key = anchor.sticky_key
    return _record_prompt_cache_resolution(_PromptCacheResolution(anchor.sticky_key, "derived", anchor.outcome))


def _record_prompt_cache_resolution(resolution: _PromptCacheResolution) -> _PromptCacheResolution:
    if PROMETHEUS_AVAILABLE and prompt_cache_key_derivation_total is not None:
        prompt_cache_key_derivation_total.labels(outcome=resolution.outcome).inc()
    return resolution


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
    resolution = _resolve_prompt_cache_key(
        payload,
        openai_cache_affinity=openai_cache_affinity,
        api_key=api_key,
        max_age_seconds=openai_cache_affinity_max_age_seconds,
    )
    cache_key = resolution.sticky_key
    cache_key_source = resolution.source
    turn_state_key = _sticky_key_from_turn_state_header(headers)
    if turn_state_key and turn_state_key != synthesized_turn_state:
        policy = _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    elif (
        thread_affinity := _thread_codex_session_affinity(
            headers,
            enabled=codex_session_affinity,
            max_age_seconds=openai_cache_affinity_max_age_seconds,
        )
    ) is not None:
        policy = thread_affinity
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
            prompt_cache_key_source=cache_key_source,
        )
    elif sticky_threads_enabled:
        policy = _AffinityPolicy(
            key=cache_key,
            kind=StickySessionKind.STICKY_THREAD,
            reallocate_sticky=True,
            prompt_cache_key_source=cache_key_source,
        )
    elif turn_state_key is not None and turn_state_key == synthesized_turn_state:
        policy = _AffinityPolicy(
            key=turn_state_key,
            kind=StickySessionKind.CODEX_SESSION,
            codex_session_source="turn_state",
        )
    else:
        policy = _AffinityPolicy()
    if (
        # The raw row this escape hatch retires is the process-session key.
        # Current Codex also sends thread-id, so locality source is often
        # thread_header; that must not hide the process-session exception.
        # An explicit turn-state header stays hard even with the same marker.
        policy.codex_session_source in {"session_header", "thread_header"}
        and (
            policy.codex_session_source == "session_header"
            or _codex_backend_identity(headers).process_session is not None
        )
        and _request_allows_unavailable_legacy_owner_abandonment(payload)
    ):
        policy = replace(policy, abandon_unavailable_legacy_owner=True)
    policy = replace(policy, prompt_cache_derivation_outcome=resolution.outcome)
    return _affinity_with_payload_continuity(policy, payload)
