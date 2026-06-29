from __future__ import annotations

from app.modules.proxy._service.http_bridge.helpers import _extract_model_class, _log_http_bridge_event
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.support import _HTTPBridgeSession


class _HTTPBridgeAccountSessionsMixin:
    async def close_http_bridge_sessions_for_account(self: _HTTPBridgeServiceProtocol, account_id: str) -> int:
        sessions_to_close: list[_HTTPBridgeSession] = []
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

        for session in sessions_to_close:
            await self._close_http_bridge_session_bounded(session, reason="account_binding_changed")
        return len(sessions_to_close)
