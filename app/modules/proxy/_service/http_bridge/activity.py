from __future__ import annotations

from typing import Any

from app.modules.proxy._service.http_bridge.helpers import (
    _close_http_bridge_session_bounded,
    _http_bridge_pending_count_nowait,
    _http_bridge_pending_state_is_stale,
    _http_bridge_request_counts_against_queue,
    _log_http_bridge_event,
    _raise_http_bridge_incompatible_admission_handoff,
    _record_http_bridge_unanchored_handoff_recovery,
    http_bridge_activity_snapshot_nowait,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.support import _http_bridge_session_supports_service_tier, _HTTPBridgeSession
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
