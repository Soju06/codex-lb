from __future__ import annotations

from app.modules.proxy._service.http_bridge.helpers import (
    _extract_model_class,
    _http_bridge_session_has_account_lease_releases,
    _log_http_bridge_event,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.support import _HTTPBridgeSession


class _HTTPBridgeAccountSessionsMixin:
    async def close_http_bridge_sessions_for_account(self: _HTTPBridgeServiceProtocol, account_id: str) -> int:
        sessions_to_close: list[_HTTPBridgeSession] = []
        scheduled_session_ids: set[int] = set()
        async with self._http_bridge_lock:
            for key, session in tuple(self._http_bridge_sessions.items()):
                if session.account.id != account_id:
                    continue
                detached = self._detach_http_bridge_session_locked(key, expected_session=session)
                if detached is None:
                    continue
                _log_http_bridge_event(
                    "evict_account_binding_changed",
                    key,
                    account_id=session.account.id,
                    model=session.request_model,
                    cache_key_family=key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                sessions_to_close.append(detached)
                scheduled_session_ids.add(id(detached))
            # Detached predecessors still own authenticated sockets and account
            # leases. Account invalidation must fence them even though a newer
            # generation occupies (or has vacated) their canonical key.
            for session in tuple(self._http_bridge_detached_sessions.values()):
                if id(session) in scheduled_session_ids:
                    continue
                # Failed reconnects can retain a replacement account's lease
                # before changing the socket owner. Only closed generations
                # may be selected by that lease; a live A owner must not be
                # retired by cleanup for B.
                retained_account_matches = session.closed and any(
                    lease.account_id == account_id for lease in session.pending_account_lease_releases
                )
                if session.account.id != account_id and not retained_account_matches:
                    continue
                close_task = session.resource_close_task
                if (
                    close_task is not None
                    and (not close_task.done() or (not close_task.cancelled() and close_task.exception() is None))
                    and not _http_bridge_session_has_account_lease_releases(session)
                ):
                    # ``closed`` only rejects admission. A live close task (or a
                    # successfully completed one awaiting registry finalization)
                    # is the proof that this detached generation is already owned.
                    continue
                session.closed = True
                _log_http_bridge_event(
                    "evict_account_binding_changed",
                    session.key,
                    account_id=session.account.id,
                    model=session.request_model,
                    cache_key_family=session.key.affinity_kind,
                    model_class=_extract_model_class(session.request_model) if session.request_model else None,
                )
                sessions_to_close.append(session)
                scheduled_session_ids.add(id(session))

        for session in sessions_to_close:
            await self._close_http_bridge_session_bounded(session, reason="account_binding_changed")
        return len(sessions_to_close)
