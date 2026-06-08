from __future__ import annotations

from typing import Any, Protocol


class _StreamingServiceProtocol(Protocol):
    _acquire_account_response_create_lease_or_overload: Any
    _cancel_api_key_reservation_heartbeat_task: Any
    _encryptor: Any
    _ensure_fresh_with_budget: Any
    _get_work_admission: Any
    _handle_stream_error: Any
    _load_balancer: Any
    _maybe_touch_api_key_reservation: Any
    _raise_for_unsupported_input_image_references: Any
    _release_unsettled_stream_api_key_usage: Any
    _resolve_file_account_for_responses: Any
    _resolve_upstream_route_for_account: Any
    _resolve_websocket_previous_response_owner: Any
    _run_api_key_reservation_heartbeat: Any
    _schedule_cancel_safe_cleanup: Any
    _select_account_with_budget_compatible: Any
    _settle_stream_api_key_usage: Any
    _stream_once: Any
    _stream_with_retry: Any
    _write_request_log: Any
    _write_stream_preflight_error: Any
