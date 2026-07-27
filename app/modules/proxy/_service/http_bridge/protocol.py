from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.core.clients.proxy import ProxyResponseError
from app.core.openai.requests import ResponsesRequest
from app.db.models import Account
from app.modules.proxy._service.support import _HTTPBridgeSession, _HTTPBridgeSessionKey
from app.modules.proxy.durable_bridge_repository import DurableBridgeAliasRegistrationReceipt
from app.modules.proxy.load_balancer import AccountSelection


class _HTTPBridgeServiceProtocol(Protocol):
    _repo_factory: Any
    _encryptor: Any
    _load_balancer: Any
    _http_client: Any
    _ring_membership: Any
    _durable_bridge: Any
    _durable_bridge_coordinator: Any
    _http_bridge_owner_client: Any
    _http_bridge_sessions: Any
    _http_bridge_inflight_sessions: Any
    _http_bridge_turn_state_index: Any
    _http_bridge_previous_response_index: Any
    _sessions: Any
    _session_lock: Any
    _pending_lock: Any
    _inflight_session_creates: Any
    _background_cleanup_tasks: Any
    _http_bridge_draining: bool
    _http_bridge_lock: Any
    _work_admission: Any

    async def _ensure_fresh_with_budget(
        self, account: Account, *, force: bool = False, timeout_seconds: float | None = None
    ) -> Account: ...

    async def _select_account_with_budget_for_stream(self, deadline: float, **kwargs: Any) -> AccountSelection: ...

    def _raise_for_unsupported_input_image_references(self, payload: ResponsesRequest) -> None: ...
    async def _resolve_file_account_for_responses(
        self, payload: ResponsesRequest, headers: Mapping[str, str]
    ) -> str | None: ...
    async def _fail_pending_websocket_requests(self, *args: Any, **kwargs: Any) -> None: ...
    async def _finalize_websocket_request_state(self, *args: Any, **kwargs: Any) -> None: ...
    async def _next_websocket_receive_timeout(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _close_http_bridge_session_bounded(self, session: _HTTPBridgeSession, *, reason: str) -> None: ...
    async def _refresh_durable_http_bridge_session(self, session: _HTTPBridgeSession) -> None: ...
    def _http_bridge_pending_count_nowait(self, session: _HTTPBridgeSession, *, context: str) -> int | None: ...
    def _detach_http_bridge_session_locked(
        self,
        key: _HTTPBridgeSessionKey,
        *,
        expected_session: _HTTPBridgeSession | None = None,
        mark_closed: bool = True,
    ) -> _HTTPBridgeSession | None: ...
    def _unregister_http_bridge_turn_states_locked(self, session: _HTTPBridgeSession) -> None: ...
    def _unregister_http_bridge_previous_response_ids_locked(self, session: _HTTPBridgeSession) -> None: ...
    async def _register_http_bridge_turn_state_impl(
        self,
        session: _HTTPBridgeSession,
        turn_state: str,
    ) -> bool: ...
    async def _register_http_bridge_turn_state_core(
        self,
        session: _HTTPBridgeSession,
        turn_state: str,
        *,
        reversible: bool,
    ) -> tuple[bool, DurableBridgeAliasRegistrationReceipt | None]: ...
    async def _register_http_bridge_previous_response_id_impl(
        self,
        session: _HTTPBridgeSession,
        response_id: str,
        *,
        input_item_count: int | None = None,
        input_full_fingerprint: str | None = None,
    ) -> bool: ...
    def _schedule_http_bridge_session_closes(
        self,
        sessions: list[_HTTPBridgeSession],
        *,
        reason: str,
    ) -> None: ...
    async def _open_upstream_websocket_with_budget(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _resolve_websocket_previous_response_owner(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _acquire_request_state_response_create_admission(self, *args: Any, **kwargs: Any) -> None: ...
    async def _handle_proxy_error(self, account: Account, exc: ProxyResponseError) -> None: ...
    async def _handle_stream_error(
        self, account: Account, error: Any, code: str, http_status: int | None = None
    ) -> Any: ...

    _write_request_log: Any

    def _build_additional_rate_limits(self, *args: Any, **kwargs: Any) -> Any: ...
    def _start_request_state_api_key_reservation_heartbeat(self, *args: Any, **kwargs: Any) -> None: ...
    def _cancel_request_state_api_key_reservation_heartbeat(self, *args: Any, **kwargs: Any) -> None: ...
    async def _maybe_touch_request_state_api_key_reservation(self, *args: Any, **kwargs: Any) -> None: ...
    async def _reserve_websocket_api_key_usage(self, *args: Any, **kwargs: Any) -> Any: ...
    async def _release_websocket_reservation(self, *args: Any, **kwargs: Any) -> None: ...
    async def _release_websocket_request_state_reservation(self, *args: Any, **kwargs: Any) -> None: ...
    def _schedule_cancel_safe_cleanup(self, *args: Any, **kwargs: Any) -> None: ...
