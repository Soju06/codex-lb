from __future__ import annotations

import logging
import time
import weakref
from dataclasses import dataclass
from typing import Any

from app.modules.proxy._service.http_bridge.helpers import _log_http_bridge_event
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_HTTP,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
    _WebSocketRequestState,
)
from app.modules.proxy.affinity import _extract_model_class

logger = logging.getLogger("app.modules.proxy.service")

# Quarantine is a bounded, in-memory, session-scoped (never account-scoped)
# marker for HTTP bridge session keys that have proven silent/wedged: a later
# request must not re-attach to them and must take the existing fresh
# session/no-anchor path instead (#1534). It complements — and never replaces
# — the in-flight recovery machinery: the eventless watchdog and bounded
# replay (#1394) recover the request that is currently stuck, the fenced
# durable-anchor clear (#1563) stops a *fully eventless* full-resend anchor
# from being re-injected, and the durable retry circuit backs off in-place
# retries. Quarantine covers what those leave open: the reattached stream
# that delivers response events but never gets ``response.created`` (the
# ``response_event_count == 0`` gates in the stale/eventless detection never
# trip on it), and the repeated-wedge case where consecutive eventless
# timeouts keep rebuilding the same reattach.
_HTTP_BRIDGE_QUARANTINE_TTL_SECONDS = 600.0
_HTTP_BRIDGE_QUARANTINE_EVENTLESS_TIMEOUT_THRESHOLD = 2
_HTTP_BRIDGE_QUARANTINE_MAX_ENTRIES = 1024

_HTTP_BRIDGE_QUARANTINE_WEDGED_REATTACH_REASON = "reattach_missing_response_created"
_HTTP_BRIDGE_QUARANTINE_REPEATED_EVENTLESS_REASON = "repeated_eventless_timeout"


@dataclass(slots=True)
class _HTTPBridgeQuarantineEntry:
    generation: int = 0
    # Keep a weak lifetime token rather than the owning session's ``id()`` or
    # a strong session reference. A detached session can finish after the key
    # is reused, and CPython may recycle an old object's integer id before that
    # completion arrives. The weak reference keeps the bounded quarantine
    # registry from retaining a detached websocket session until TTL expiry.
    owner_ref: weakref.ReferenceType[_HTTPBridgeSession] | None = None
    quarantined_until: float = 0.0
    consecutive_eventless_timeouts: int = 0
    last_touched_monotonic: float = 0.0
    reason: str | None = None


def _http_bridge_quarantine_registry(
    service: Any,
) -> dict[_HTTPBridgeSessionKey, _HTTPBridgeQuarantineEntry]:
    registry = getattr(service, "_http_bridge_quarantined_keys", None)
    if registry is None:
        registry = {}
        service._http_bridge_quarantined_keys = registry
    return registry


def _next_http_bridge_quarantine_generation(
    service: Any,
    registry: dict[_HTTPBridgeSessionKey, _HTTPBridgeQuarantineEntry],
) -> int:
    generation = getattr(service, "_http_bridge_quarantine_generation_counter", None)
    if generation is None:
        generation = max((entry.generation for entry in registry.values()), default=0)
    generation += 1
    service._http_bridge_quarantine_generation_counter = generation
    return generation


def _prune_http_bridge_quarantine_registry(
    registry: dict[_HTTPBridgeSessionKey, _HTTPBridgeQuarantineEntry],
    now: float,
) -> None:
    expiry = now - _HTTP_BRIDGE_QUARANTINE_TTL_SECONDS
    for key, entry in list(registry.items()):
        if entry.last_touched_monotonic <= expiry and entry.quarantined_until <= now:
            registry.pop(key, None)
    overflow = len(registry) - _HTTP_BRIDGE_QUARANTINE_MAX_ENTRIES
    if overflow > 0:
        for stale_key in sorted(registry, key=lambda candidate: registry[candidate].last_touched_monotonic)[:overflow]:
            registry.pop(stale_key, None)


def _http_bridge_request_state_wedged_reattach(request_state: _WebSocketRequestState) -> bool:
    """Identify the #1534 wedge shape on a request that is being failed/retired.

    A reattached stream (proxy-injected ``previous_response_id``) whose
    ``response.create`` was sent and that observed upstream response events,
    but whose ``response.created`` was never assigned. This is only evaluated
    when the request is already being failed or its session retired — never
    against a live owned turn — so legitimate long event gaps (for example
    deferred-reasoning streams) can never trip it, and any request whose
    ``response.created`` was observed (``response_id`` or created latency set)
    is excluded by construction.
    """
    return (
        getattr(request_state, "transport", None) == _REQUEST_TRANSPORT_HTTP
        and not getattr(request_state, "skip_request_log", False)
        and getattr(request_state, "proxy_injected_previous_response_id", False)
        and getattr(request_state, "response_create_sent_at", None) is not None
        and getattr(request_state, "response_id", None) is None
        and getattr(request_state, "latency_response_created_ms", None) is None
        and getattr(request_state, "response_event_count", 0) > 0
    )


def _http_bridge_session_key_quarantined(service: Any, key: _HTTPBridgeSessionKey) -> bool:
    registry = _http_bridge_quarantine_registry(service)
    now = time.monotonic()
    _prune_http_bridge_quarantine_registry(registry, now)
    entry = registry.get(key)
    return entry is not None and entry.quarantined_until > now


def _http_bridge_quarantine_generation(service: Any, key: _HTTPBridgeSessionKey) -> int | None:
    """Return the active quarantine generation observed for one recovery."""
    registry = _http_bridge_quarantine_registry(service)
    now = time.monotonic()
    _prune_http_bridge_quarantine_registry(registry, now)
    entry = registry.get(key)
    if entry is None or entry.quarantined_until <= now:
        return None
    return entry.generation


def _quarantine_http_bridge_session(service: Any, session: _HTTPBridgeSession, *, reason: str) -> None:
    """Quarantine a bridge session that has proven silent/wedged.

    Session-scoped only: no account-health writes happen here, and the entry
    is bounded by TTL, a registry size cap, and the healthy-completion clear.
    """
    now = time.monotonic()
    registry = _http_bridge_quarantine_registry(service)
    entry = registry.setdefault(session.key, _HTTPBridgeQuarantineEntry())
    already_quarantined = entry.quarantined_until > now
    entry.generation = _next_http_bridge_quarantine_generation(service, registry)
    # Keep a weak session identity alongside the generation. A detached
    # predecessor can still finish a response after a replacement has reused
    # this key; that completion must not clear the replacement's newer entry.
    entry.owner_ref = weakref.ref(session)
    entry.quarantined_until = max(entry.quarantined_until, now + _HTTP_BRIDGE_QUARANTINE_TTL_SECONDS)
    entry.last_touched_monotonic = now
    entry.reason = reason
    _prune_http_bridge_quarantine_registry(registry, now)
    session.quarantined = True
    if already_quarantined:
        return
    _log_http_bridge_event(
        "session_quarantined",
        session.key,
        account_id=session.account.id,
        model=session.request_model,
        detail=f"reason={reason}, ttl_seconds={_HTTP_BRIDGE_QUARANTINE_TTL_SECONDS:.0f}",
        cache_key_family=session.key.affinity_kind,
        model_class=_extract_model_class(session.request_model) if session.request_model else None,
    )


def _record_http_bridge_quarantine_wedged_pending(
    service: Any,
    session: _HTTPBridgeSession,
    request_states: Any,
) -> bool:
    """Quarantine the session when a failed/retired pending request proves the wedge shape."""
    if not any(_http_bridge_request_state_wedged_reattach(request_state) for request_state in request_states):
        return False
    _quarantine_http_bridge_session(
        service,
        session,
        reason=_HTTP_BRIDGE_QUARANTINE_WEDGED_REATTACH_REASON,
    )
    return True


def _record_http_bridge_quarantine_eventless_timeout(service: Any, session: _HTTPBridgeSession) -> None:
    """Count a ``missing_response_created_timeout`` retire; quarantine on repeats.

    The first eventless timeout is left to the merged recovery machinery
    (bounded pre-created retry, fenced durable-anchor clear). A second
    consecutive one for the same session key proves that path is also
    rebuilding a wedged attach, so later requests must stop re-attaching.
    """
    now = time.monotonic()
    registry = _http_bridge_quarantine_registry(service)
    # Prune before touching the entry: a strike whose TTL already lapsed must
    # not be resurrected into a "consecutive" second strike hours later.
    _prune_http_bridge_quarantine_registry(registry, now)
    entry = registry.setdefault(session.key, _HTTPBridgeQuarantineEntry())
    entry.consecutive_eventless_timeouts += 1
    entry.last_touched_monotonic = now
    if entry.consecutive_eventless_timeouts < _HTTP_BRIDGE_QUARANTINE_EVENTLESS_TIMEOUT_THRESHOLD:
        return
    _quarantine_http_bridge_session(
        service,
        session,
        reason=_HTTP_BRIDGE_QUARANTINE_REPEATED_EVENTLESS_REASON,
    )


def _clear_http_bridge_quarantine(
    service: Any,
    session: _HTTPBridgeSession,
    *,
    additional_key: _HTTPBridgeSessionKey | None = None,
    additional_key_generation: int | None = None,
) -> None:
    """A completed response disproves the current and recovery-origin wedges.

    ``additional_key_generation`` is the quarantine generation the recovery
    observed for ``additional_key`` when the retry was authorized, and ``None``
    means it observed no quarantine at all.  Either way the recovery-origin
    clear is fenced: only an exact generation match clears it.
    """
    registry = _http_bridge_quarantine_registry(service)
    keys = (session.key,) if additional_key is None or additional_key == session.key else (session.key, additional_key)
    for key in keys:
        entry = registry.get(key)
        if additional_key is not None and key == additional_key:
            # A stale-anchor recovery snapshots the quarantine generation of
            # its recovery-origin key when the retry is authorized, and that
            # snapshot is the only proof that this completion may clear the
            # entry.  ``None`` is the observed *absence* of a quarantine, not
            # a missing fence: any entry sitting on the key now was raced in
            # while the retry was in flight and must survive.  A mismatching
            # generation is a newer quarantine for the same reason.  This
            # holds for both recovery shapes — a distinct recovery key and the
            # completing session's own key — and is checked before mutating
            # the session marker or popping the registry entry.
            if additional_key_generation is None or entry is None or entry.generation != additional_key_generation:
                continue
        if key == session.key:
            active_sessions = getattr(service, "_http_bridge_sessions", None)
            is_current_primary_session = isinstance(active_sessions, dict) and active_sessions.get(key) is session
            if (
                entry is not None
                and entry.owner_ref is not None
                and entry.owner_ref() is not session
                and not is_current_primary_session
            ):
                # Only the session that created the primary-key entry may
                # clear it.  A detached predecessor can finish after a
                # replacement has reused the key, even if its per-session flag
                # was already reset; the canonical registry fences that
                # completion from popping the replacement's newer primary
                # entry.  A healthy canonical replacement may still clear the
                # key after its own response completes.
                continue
            session.quarantined = False
        entry = registry.pop(key, None)
        if entry is None or entry.quarantined_until <= time.monotonic():
            continue
        _log_http_bridge_event(
            "session_quarantine_cleared",
            key,
            account_id=session.account.id,
            model=session.request_model,
            detail=f"reason={entry.reason}",
            cache_key_family=key.affinity_kind,
            model_class=_extract_model_class(session.request_model) if session.request_model else None,
        )
