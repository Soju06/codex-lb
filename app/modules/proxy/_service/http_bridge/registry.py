from __future__ import annotations

from typing import Any

from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_previous_response_alias_key,
    _http_bridge_turn_state_alias_key,
)


def unregister_turn_states_locked(service: Any, session: Any) -> None:
    current_session = service._http_bridge_sessions.get(session.key)
    for alias in tuple(session.downstream_turn_state_aliases):
        alias_key = _http_bridge_turn_state_alias_key(alias, session.key.api_key_id)
        if current_session is not None and current_session is not session:
            if alias in current_session.downstream_turn_state_aliases:
                continue
        if service._http_bridge_turn_state_index.get(alias_key) == session.key:
            service._http_bridge_turn_state_index.pop(alias_key, None)
    session.downstream_turn_state_aliases.clear()


def unregister_previous_response_ids_locked(service: Any, session: Any) -> None:
    current_session = service._http_bridge_sessions.get(session.key)
    for response_id in tuple(session.previous_response_ids):
        alias_key = _http_bridge_previous_response_alias_key(response_id, session.key.api_key_id)
        if current_session is not None and current_session is not session:
            if response_id in current_session.previous_response_ids:
                continue
        if service._http_bridge_previous_response_index.get(alias_key) == session.key:
            service._http_bridge_previous_response_index.pop(alias_key, None)
    session.previous_response_ids.clear()
