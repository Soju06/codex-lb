from __future__ import annotations

import asyncio
from typing import Any

from app.core.clients.proxy import ProxyResponseError
from app.core.resilience.overload import local_overload_error
from app.modules.proxy._service.http_bridge.helpers import (
    _close_http_bridge_session_bounded,
    _http_bridge_capacity_generation_count,
    _http_bridge_pending_count_nowait,
    _http_bridge_pending_state_is_stale,
    _http_bridge_request_counts_against_queue,
    _log_http_bridge_event,
    _raise_http_bridge_incompatible_admission_handoff,
    _record_http_bridge_unanchored_handoff_recovery,
    http_bridge_activity_snapshot_nowait,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.support import (
    _http_bridge_session_supports_service_tier,
    _HTTPBridgeSession,
    _HTTPBridgeSessionKey,
)
from app.modules.proxy.affinity import _extract_model_class


class _HTTPBridgeActivityMixin:
    _http_bridge_pending_state_is_stale = staticmethod(_http_bridge_pending_state_is_stale)

    def _recover_http_bridge_incompatible_admission_handoff(
        self: Any,
        key: Any,
        existing: Any,
        force_durable_takeover: bool,
        original_request_unanchored: bool,
        request_model: str | None,
        api_key: Any,
        incoming_turn_state: str | None,
        previous_response_id: str | None,
        preferred_account_id: str | None,
        require_preferred_account: bool,
        request_service_tier: str | None,
    ) -> tuple[Any, bool]:
        if original_request_unanchored and existing is not None:
            detached = self._detach_http_bridge_session_locked(key, expected_session=existing)
            if detached is not None:
                force_durable_takeover = True
                _record_http_bridge_unanchored_handoff_recovery(reason="closed_admission_handoff")
                _log_http_bridge_event(
                    "unanchored_handoff_recovery",
                    key,
                    account_id=detached.account.id,
                    model=request_model,
                    detail="outcome=retired_closed_admission_handoff",
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(request_model) if request_model else None,
                    owner_check_applied=False,
                )
                self._schedule_http_bridge_session_closes([detached], reason="unanchored_handoff_recovery")
            return None, force_durable_takeover

        _raise_http_bridge_incompatible_admission_handoff(
            session=existing,
            key=key,
            api_key=api_key,
            incoming_turn_state=incoming_turn_state,
            previous_response_id=previous_response_id,
            preferred_account_id=preferred_account_id,
            require_preferred_account=require_preferred_account,
            request_service_tier=request_service_tier,
            service_tier_supported=_http_bridge_session_supports_service_tier(
                existing,
                request_model=request_model,
                request_service_tier=request_service_tier,
            ),
        )
        raise AssertionError("incompatible admission handoff must raise")

    async def _close_http_bridge_session_bounded(
        self: Any,
        session: _HTTPBridgeSession,
        *,
        reason: str,
    ) -> None:
        await _close_http_bridge_session_bounded(self, session, reason=reason)

    def _http_bridge_active_capacity_error(
        self: _HTTPBridgeServiceProtocol,
        *,
        key: _HTTPBridgeSessionKey,
        request_model: str | None,
    ) -> ProxyResponseError:
        _log_http_bridge_event(
            "capacity_exhausted_active_sessions",
            key,
            account_id=None,
            model=request_model,
            pending_count=_http_bridge_capacity_generation_count(self),
            cache_key_family=key.affinity_kind,
            model_class=_extract_model_class(request_model) if request_model else None,
        )
        return ProxyResponseError(
            429,
            local_overload_error(
                "HTTP responses session bridge has no idle capacity",
                code="capacity_exhausted_active_sessions",
            ),
        )

    def _http_bridge_forced_close_must_finish_before_create(
        self: _HTTPBridgeServiceProtocol,
        forced_replacement: bool,
        max_sessions: int,
    ) -> bool:
        # Detachment retains capacity. A forced replacement at the cap must
        # finish closing its idle predecessor before enforcing the same cap.
        return forced_replacement and _http_bridge_capacity_generation_count(self) >= max_sessions

    async def _enforce_http_bridge_capacity_after_planned_closes(
        self: _HTTPBridgeServiceProtocol,
        *,
        key: _HTTPBridgeSessionKey,
        inflight_future: asyncio.Future[_HTTPBridgeSession] | None,
        max_sessions: int,
        request_model: str | None,
    ) -> None:
        assert inflight_future is not None
        async with self._http_bridge_lock:
            if (
                self._http_bridge_inflight_sessions.get(key) is not inflight_future
                or _http_bridge_capacity_generation_count(self) <= max_sessions
            ):
                return
            # Planned evictions are discounted only to reserve this creation
            # slot. A bounded close may return on timeout while the detached
            # socket and leases remain live, so registry ownership wins here.
            _log_http_bridge_event(
                "capacity_exhausted_after_lru_close",
                key,
                account_id=None,
                model=request_model,
                pending_count=_http_bridge_capacity_generation_count(self),
                cache_key_family=key.affinity_kind,
                model_class=_extract_model_class(request_model) if request_model else None,
            )
            capacity_error = ProxyResponseError(
                429,
                local_overload_error(
                    "HTTP responses session bridge has no idle capacity",
                    code="capacity_exhausted_active_sessions",
                ),
            )
        await self._fail_http_bridge_inflight_session_creation(key, inflight_future, capacity_error)
        raise capacity_error

    async def _http_bridge_pending_count(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> int:
        async with session.pending_lock:
            visible_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            return max(visible_pending_count, session.queued_request_count)

    def http_bridge_activity_snapshot_nowait(self: _HTTPBridgeServiceProtocol) -> dict[str, int | bool]:
        return http_bridge_activity_snapshot_nowait(self)

    def _http_bridge_pending_count_nowait(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        *,
        context: str,
    ) -> int | None:
        return _http_bridge_pending_count_nowait(session, context=context)
