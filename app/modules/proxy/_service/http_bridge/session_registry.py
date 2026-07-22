from __future__ import annotations

import asyncio
import logging

from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import Settings
from app.core.errors import openai_error
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    bridge_durable_recover_total,
    bridge_instance_mismatch_total,
)
from app.db.models import StickySessionKind
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_durable_lease_ttl_seconds,
    _http_bridge_live_previous_response_alias_owner,
    _http_bridge_live_turn_state_alias_owner,
    _http_bridge_owner_lookup_unavailable_error_envelope,
    _http_bridge_previous_response_alias_key,
    _http_bridge_turn_state_alias_key,
    _is_missing_durable_bridge_table_error,
    _log_http_bridge_event,
    _persist_http_bridge_previous_response_alias,
    _persist_http_bridge_turn_state_alias,
    _reconcile_durable_http_bridge_ownership,
    _record_bridge_reattach,
    _register_http_bridge_turn_state_aliases_locked,
    _renew_durable_http_bridge_lease,
    _track_alias_registration,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.http_bridge.service_stubs import (
    _headers_with_turn_state,
    _service_get_settings,
)
from app.modules.proxy._service.support import _HTTPBridgeSession, _HTTPBridgeSessionKey
from app.modules.proxy.affinity import _AffinityPolicy, _extract_model_class
from app.modules.proxy.continuity import (
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS,
    is_http_bridge_account_neutral_replay,
    without_http_bridge_session_affinity_headers,
)
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeLookup
from app.modules.proxy.durable_bridge_repository import (
    DurableBridgeAliasRegistration,
    DurableBridgeAliasRegistrationReceipt,
)

logger = logging.getLogger("app.modules.proxy.service")


def _recovery_can_rebind_live_alias(session: _HTTPBridgeSession) -> bool:
    return session.key.affinity_kind in HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS or (
        is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )
    )


def _requires_durable_recovery_alias_serialization(session: _HTTPBridgeSession) -> bool:
    return (
        session.durable_session_id is not None
        and session.durable_owner_epoch is not None
        and is_http_bridge_account_neutral_replay(
            kind=session.key.affinity_kind,
            key=session.key.affinity_key,
        )
    )


class _HTTPBridgeSessionRegistryMixin:
    async def _register_http_bridge_turn_state(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        turn_state: str,
    ) -> bool:
        if _requires_durable_recovery_alias_serialization(session):
            async with session.recovery_alias_lock:
                return await self._register_http_bridge_turn_state_impl(session, turn_state)
        return await self._register_http_bridge_turn_state_impl(session, turn_state)

    async def _register_http_bridge_turn_state_impl(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        turn_state: str,
    ) -> bool:
        registered, _receipt = await self._register_http_bridge_turn_state_core(
            session,
            turn_state,
            reversible=False,
        )
        return registered

    async def _register_http_bridge_recovery_turn_state_locked(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        turn_state: str,
    ) -> tuple[bool, DurableBridgeAliasRegistrationReceipt | None]:
        if not _requires_durable_recovery_alias_serialization(session):
            return False, None
        return await self._register_http_bridge_turn_state_core(
            session,
            turn_state,
            reversible=True,
        )

    async def _register_http_bridge_turn_state_core(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        turn_state: str,
        *,
        reversible: bool,
    ) -> tuple[bool, DurableBridgeAliasRegistrationReceipt | None]:
        defer_durable_publication = False
        deferred_live_alias_owner: _HTTPBridgeSession | None = None
        async with self._http_bridge_lock:
            if session.closed:
                return False, None
            account_neutral_recovery = is_http_bridge_account_neutral_replay(
                kind=session.key.affinity_kind,
                key=session.key.affinity_key,
            )
            if account_neutral_recovery and (session.durable_session_id is None or session.durable_owner_epoch is None):
                return False, None
            defer_durable_publication = (
                account_neutral_recovery
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
            )
            live_alias_owner = _http_bridge_live_turn_state_alias_owner(self, session, turn_state)
            if live_alias_owner is not None:
                can_rebind_recovery_alias = account_neutral_recovery and _recovery_can_rebind_live_alias(
                    live_alias_owner
                )
                if not can_rebind_recovery_alias:
                    return not account_neutral_recovery, None
                if defer_durable_publication:
                    deferred_live_alias_owner = live_alias_owner
                else:
                    live_alias_owner.downstream_turn_state_aliases.discard(turn_state)
                    live_alias_owner.turn_state_alias_registration_generations.pop(turn_state, None)
                    if live_alias_owner.downstream_turn_state == turn_state:
                        live_alias_owner.downstream_turn_state = None
            if account_neutral_recovery:
                session.codex_session = True
                session.idle_ttl_seconds = max(
                    session.idle_ttl_seconds,
                    float(_service_get_settings().http_responses_session_bridge_codex_idle_ttl_seconds),
                )
                session.headers = without_http_bridge_session_affinity_headers(session.headers)
            registration_generation = _track_alias_registration(session, turn_state, turn_state=True)
            if not defer_durable_publication:
                session.downstream_turn_state_aliases.add(turn_state)
                if session.downstream_turn_state is None:
                    session.downstream_turn_state = turn_state
                if live_alias_owner is not None:
                    alias_key = _http_bridge_turn_state_alias_key(turn_state, session.key.api_key_id)
                    self._http_bridge_turn_state_index[alias_key] = session.key
                _register_http_bridge_turn_state_aliases_locked(self, session)
        if session.durable_session_id is None or session.durable_owner_epoch is None:
            return True, None
        durable_result, receipt = await _persist_http_bridge_turn_state_alias(
            self,
            session,
            turn_state=turn_state,
            registration_generation=registration_generation,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
            local_alias_was_published=not defer_durable_publication,
            reversible=reversible,
        )
        if not defer_durable_publication:
            return True, receipt
        if durable_result != DurableBridgeAliasRegistration.REGISTERED:
            return False, receipt
        async with self._http_bridge_lock:
            if (
                session.closed
                or self._http_bridge_sessions.get(session.key) is not session
                or session.turn_state_alias_registration_generations.get(turn_state) != registration_generation
            ):
                if session.turn_state_alias_registration_generations.get(turn_state) == registration_generation:
                    session.turn_state_alias_registration_generations.pop(turn_state, None)
                return False, receipt
            current_live_owner = _http_bridge_live_turn_state_alias_owner(self, session, turn_state)
            if (
                current_live_owner is not None
                and current_live_owner is not deferred_live_alias_owner
                and not _recovery_can_rebind_live_alias(current_live_owner)
            ):
                session.turn_state_alias_registration_generations.pop(turn_state, None)
                return False, receipt
            if current_live_owner is not None:
                current_live_owner.downstream_turn_state_aliases.discard(turn_state)
                current_live_owner.turn_state_alias_registration_generations.pop(turn_state, None)
                if current_live_owner.downstream_turn_state == turn_state:
                    current_live_owner.downstream_turn_state = None
            session.downstream_turn_state_aliases.add(turn_state)
            if session.downstream_turn_state is None:
                session.downstream_turn_state = turn_state
            alias_key = _http_bridge_turn_state_alias_key(turn_state, session.key.api_key_id)
            self._http_bridge_turn_state_index[alias_key] = session.key
            _register_http_bridge_turn_state_aliases_locked(self, session)
        return True, receipt

    async def _register_http_bridge_previous_response_id(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        response_id: str,
        *,
        input_item_count: int | None = None,
        input_full_fingerprint: str | None = None,
    ) -> bool:
        if _requires_durable_recovery_alias_serialization(session):
            async with session.recovery_alias_lock:
                return await self._register_http_bridge_previous_response_id_impl(
                    session,
                    response_id,
                    input_item_count=input_item_count,
                    input_full_fingerprint=input_full_fingerprint,
                )
        return await self._register_http_bridge_previous_response_id_impl(
            session,
            response_id,
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
        )

    async def _register_http_bridge_previous_response_id_impl(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        response_id: str,
        *,
        input_item_count: int | None = None,
        input_full_fingerprint: str | None = None,
    ) -> bool:
        stripped_response_id = response_id.strip()
        if not stripped_response_id:
            return False
        defer_durable_publication = False
        deferred_live_alias_owner: _HTTPBridgeSession | None = None
        async with self._http_bridge_lock:
            if session.closed:
                return False
            if (
                session.upstream_control.retire_after_drain
                and self._http_bridge_sessions.get(session.key) is not session
            ):
                return False
            alias_key = _http_bridge_previous_response_alias_key(stripped_response_id, session.key.api_key_id)
            account_neutral_recovery = is_http_bridge_account_neutral_replay(
                kind=session.key.affinity_kind,
                key=session.key.affinity_key,
            )
            if account_neutral_recovery and (session.durable_session_id is None or session.durable_owner_epoch is None):
                return False
            defer_durable_publication = (
                account_neutral_recovery
                and session.durable_session_id is not None
                and session.durable_owner_epoch is not None
            )
            live_alias_owner = _http_bridge_live_previous_response_alias_owner(
                self,
                session,
                stripped_response_id,
            )
            if live_alias_owner is not None:
                can_rebind_recovery_alias = account_neutral_recovery and _recovery_can_rebind_live_alias(
                    live_alias_owner
                )
                if not can_rebind_recovery_alias:
                    return not account_neutral_recovery
                if defer_durable_publication:
                    deferred_live_alias_owner = live_alias_owner
                else:
                    live_alias_owner.previous_response_ids.discard(stripped_response_id)
                    live_alias_owner.previous_response_alias_registration_generations.pop(stripped_response_id, None)
            registration_generation = _track_alias_registration(session, stripped_response_id, turn_state=False)
            if not defer_durable_publication:
                self._http_bridge_previous_response_index[alias_key] = session.key
                session.previous_response_ids.add(stripped_response_id)
        if session.durable_session_id is None or session.durable_owner_epoch is None:
            return True
        durable_result = await _persist_http_bridge_previous_response_alias(
            self,
            session,
            response_id=stripped_response_id,
            registration_generation=registration_generation,
            input_item_count=input_item_count,
            input_full_fingerprint=input_full_fingerprint,
            instance_id=_service_get_settings().http_responses_session_bridge_instance_id,
            lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
            local_alias_was_published=not defer_durable_publication,
        )
        if not defer_durable_publication:
            return True
        if durable_result != DurableBridgeAliasRegistration.REGISTERED:
            return False
        async with self._http_bridge_lock:
            if (
                session.closed
                or self._http_bridge_sessions.get(session.key) is not session
                or session.previous_response_alias_registration_generations.get(stripped_response_id)
                != registration_generation
            ):
                if (
                    session.previous_response_alias_registration_generations.get(stripped_response_id)
                    == registration_generation
                ):
                    session.previous_response_alias_registration_generations.pop(stripped_response_id, None)
                return False
            current_live_owner = _http_bridge_live_previous_response_alias_owner(
                self,
                session,
                stripped_response_id,
            )
            if (
                current_live_owner is not None
                and current_live_owner is not deferred_live_alias_owner
                and not _recovery_can_rebind_live_alias(current_live_owner)
            ):
                session.previous_response_alias_registration_generations.pop(stripped_response_id, None)
                return False
            if current_live_owner is not None:
                current_live_owner.previous_response_ids.discard(stripped_response_id)
                current_live_owner.previous_response_alias_registration_generations.pop(
                    stripped_response_id,
                    None,
                )
            self._http_bridge_previous_response_index[alias_key] = session.key
            session.previous_response_ids.add(stripped_response_id)
        return True

    async def _unregister_http_bridge_turn_states(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_turn_states_locked(session)

    async def _unregister_http_bridge_previous_response_ids(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> None:
        async with self._http_bridge_lock:
            self._unregister_http_bridge_previous_response_ids_locked(session)

    def _detach_http_bridge_session_locked(
        self: _HTTPBridgeServiceProtocol,
        key: _HTTPBridgeSessionKey,
        *,
        expected_session: _HTTPBridgeSession | None = None,
        mark_closed: bool = True,
    ) -> _HTTPBridgeSession | None:
        session = self._http_bridge_sessions.get(key)
        if session is None or (expected_session is not None and session is not expected_session):
            return None
        self._http_bridge_sessions.pop(key, None)
        if mark_closed:
            session.closed = True
        self._unregister_http_bridge_turn_states_locked(session)
        self._unregister_http_bridge_previous_response_ids_locked(session)
        return session

    def _unregister_http_bridge_turn_states_locked(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> None:
        current_session = self._http_bridge_sessions.get(session.key)
        for alias in tuple(session.downstream_turn_state_aliases):
            alias_key = _http_bridge_turn_state_alias_key(alias, session.key.api_key_id)
            if (
                current_session is not None
                and current_session is not session
                and alias in current_session.downstream_turn_state_aliases
            ):
                continue
            if self._http_bridge_turn_state_index.get(alias_key) == session.key:
                self._http_bridge_turn_state_index.pop(alias_key, None)
        session.downstream_turn_state_aliases.clear()
        session.turn_state_alias_registration_generations.clear()

    def _unregister_http_bridge_previous_response_ids_locked(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> None:
        current_session = self._http_bridge_sessions.get(session.key)
        for response_id in tuple(session.previous_response_ids):
            alias_key = _http_bridge_previous_response_alias_key(response_id, session.key.api_key_id)
            if (
                current_session is not None
                and current_session is not session
                and response_id in current_session.previous_response_ids
            ):
                continue
            if self._http_bridge_previous_response_index.get(alias_key) == session.key:
                self._http_bridge_previous_response_index.pop(alias_key, None)
        session.previous_response_ids.clear()
        session.previous_response_alias_registration_generations.clear()

    def _promote_http_bridge_session_to_codex_affinity(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        *,
        turn_state: str,
        settings: Settings,
    ) -> None:
        session.affinity = _AffinityPolicy(key=turn_state, kind=StickySessionKind.CODEX_SESSION)
        session.codex_session = True
        session.downstream_turn_state = turn_state
        session.downstream_turn_state_aliases.add(turn_state)
        session.idle_ttl_seconds = max(
            session.idle_ttl_seconds,
            float(settings.http_responses_session_bridge_codex_idle_ttl_seconds),
        )
        session.headers = _headers_with_turn_state(session.headers, turn_state)

    async def _claim_durable_http_bridge_session(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        *,
        allow_takeover: bool,
        force_owner_epoch_advance: bool = False,
    ) -> None:
        current_instance = _service_get_settings().http_responses_session_bridge_instance_id
        try:
            lookup: DurableBridgeLookup | None = None
            for claim_attempt in range(2):
                lookup = await self._durable_bridge.claim_live_session(
                    session_key_kind=session.key.affinity_kind,
                    session_key_value=session.key.affinity_key,
                    api_key_id=session.key.api_key_id,
                    instance_id=current_instance,
                    lease_ttl_seconds=_http_bridge_durable_lease_ttl_seconds(),
                    account_id=session.account.id,
                    model=session.request_model,
                    service_tier=session.request_service_tier,
                    latest_turn_state=session.downstream_turn_state,
                    latest_response_id=None,
                    allow_takeover=allow_takeover,
                    force_owner_epoch_advance=force_owner_epoch_advance or claim_attempt > 0,
                )
                if lookup.owner_instance_id == current_instance:
                    break
                if not allow_takeover or claim_attempt > 0:
                    break
                await asyncio.sleep(0)
            assert lookup is not None
            if lookup.owner_instance_id != current_instance:
                _log_http_bridge_event(
                    "owner_mismatch_retry",
                    session.key,
                    account_id=None,
                    model=session.request_model,
                    detail=(
                        f"expected_instance={lookup.owner_instance_id}, "
                        f"current_instance={current_instance}, outcome=claim_rejected"
                    ),
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                    owner_check_applied=True,
                )
                if PROMETHEUS_AVAILABLE and bridge_instance_mismatch_total is not None:
                    bridge_instance_mismatch_total.labels(outcome="retry").inc()
                raise ProxyResponseError(
                    409,
                    openai_error(
                        "bridge_instance_mismatch",
                        "HTTP bridge session is owned by a different instance; retry to reach the correct replica",
                        error_type="server_error",
                    ),
                )
            session.durable_session_id = lookup.session_id
            session.durable_owner_epoch = lookup.owner_epoch
            session.headers = _headers_with_turn_state(session.headers, session.downstream_turn_state)
            if (
                PROMETHEUS_AVAILABLE
                and bridge_durable_recover_total is not None
                and allow_takeover
                and lookup.owner_epoch > 1
            ):
                bridge_durable_recover_total.labels(path="restart_takeover").inc()
                _record_bridge_reattach(path="restart_takeover", outcome="success")
            if session.key.affinity_kind == "session_header":
                await self._durable_bridge.register_session_header(
                    session_id=lookup.session_id,
                    api_key_id=session.key.api_key_id,
                    session_header=session.key.affinity_key,
                )
        except Exception as exc:
            if _is_missing_durable_bridge_table_error(exc):
                if is_http_bridge_account_neutral_replay(
                    kind=session.key.affinity_kind,
                    key=session.key.affinity_key,
                ):
                    raise ProxyResponseError(
                        502,
                        _http_bridge_owner_lookup_unavailable_error_envelope(),
                    ) from exc
                logger.warning("Durable bridge tables missing; using in-memory bridge session fallback", exc_info=True)
                return
            raise

    async def _refresh_durable_http_bridge_session(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> None:
        """Renew the durable lease; callers must hold ``self._http_bridge_lock``."""

        await _renew_durable_http_bridge_lease(self, session)

    async def reconcile_durable_http_bridge_ownership(self: _HTTPBridgeServiceProtocol) -> int:
        """Close local sessions whose durable row is owned by another instance/epoch."""

        return await _reconcile_durable_http_bridge_ownership(self)
