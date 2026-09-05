from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

import anyio

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.openai.requests import ResponsesRequest, validate_passthrough_depth
from app.core.types import JsonValue
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation
from app.db.models import Account
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WebSocketRequestState,
    _WebSocketSteeringContinuation,
    _WebSocketSteerSubmission,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket.helpers import (
    _assign_websocket_response_id,
    _release_websocket_response_create_gate,
)
from app.modules.proxy._service.websocket.protocol import _WebSocketServiceProtocol
from app.modules.proxy.api_key_usage import estimate_api_key_request_usage
from app.modules.proxy.request_policy import (
    apply_api_key_enforcement,
    normalize_unsupported_reasoning_effort,
    validate_model_access,
)

_MAX_QUEUED_STEERS = 32
logger = logging.getLogger("app.modules.proxy.service")


def steering_error(code: str, message: str) -> ProxyResponseError:
    return ProxyResponseError(400, openai_error(code, message, error_type="invalid_request_error"))


def validate_steering_input(payload: Mapping[str, JsonValue]) -> tuple[str, list[JsonValue]]:
    if set(payload) != {"type", "previous_response_id", "input"}:
        raise steering_error("invalid_input", "Steering accepts only type, previous_response_id and input.")
    parent_id = payload.get("previous_response_id")
    items: JsonValue = payload.get("input")
    if isinstance(items, str) and items:
        items = [{"role": "user", "content": [{"type": "input_text", "text": items}]}]
    if not isinstance(parent_id, str) or not parent_id.strip() or not isinstance(items, list) or not items:
        raise steering_error("invalid_input", "Steering requires a response ID and nonempty user input.")
    validate_passthrough_depth(items)
    for item in items:
        if not isinstance(item, dict) or item.get("role") != "user" or item.get("type") not in (None, "message"):
            raise steering_error("invalid_input", "Steering input must contain user messages.")
        content = item.get("content")
        if isinstance(content, str) and content:
            continue
        if not isinstance(content, list) or not content:
            raise steering_error("invalid_input", "Steering user messages require content.")
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in {"input_text", "input_image", "input_file"}:
                raise steering_error("invalid_input", "Unsupported steering content type.")
            if part["type"] == "input_text" and (not isinstance(part.get("text"), str) or not part["text"]):
                raise steering_error("invalid_input", "Steering text requires a nonempty text string.")
            if part["type"] == "input_image" and not any(
                isinstance(part.get(key), str) and part.get(key) for key in ("file_id", "image_url")
            ):
                raise steering_error("invalid_input", "Steering images require a file ID or URL.")
            if part["type"] == "input_file" and not any(
                isinstance(part.get(key), str) and part.get(key) for key in ("file_id", "file_data", "file_url")
            ):
                raise steering_error("invalid_input", "Steering files require a file ID, data or URL.")
    return parent_id.strip(), cast(list[JsonValue], items)


def steering_parent(
    parent_id: str,
    *,
    pending_requests: deque[_WebSocketRequestState],
    control: _WebSocketUpstreamControl,
) -> _WebSocketRequestState:
    continuation = control.steering_continuations.get(parent_id)
    if continuation is not None:
        if continuation.request_state not in pending_requests:
            raise steering_error("response_not_found", "The steering continuation is no longer active.")
        if len(continuation.submissions) >= _MAX_QUEUED_STEERS:
            raise steering_error(
                "too_many_pending_steers", "Wait for queued steering to be applied before submitting more."
            )
        return continuation.parent
    parent = next((state for state in pending_requests if state.response_id == parent_id), None)
    if parent is None and control.last_completed_request is not None:
        if control.last_completed_request.response_id == parent_id:
            parent = control.last_completed_request
    if parent is None:
        raise steering_error("response_not_found", "Steering requires an owned response on this WebSocket connection.")
    if parent.steering_continuation_started:
        raise steering_error("response_not_found", "Steer the successor after its response.created event.")
    if parent.model != "gpt-6-astra":
        raise steering_error("steering_not_supported", "This response does not support steering.")
    return parent


def steering_response_payload(
    parent: _WebSocketRequestState, *, parent_id: str, input_items: list[JsonValue]
) -> ResponsesRequest:
    if parent.steering_configuration is not None:
        data = dict(parent.steering_configuration)
    elif parent.request_text is not None:
        data = json.loads(parent.request_text)
    else:
        raise steering_error("response_not_found", "The original response settings are unavailable on this connection.")
    if data.get("conversation") or data.get("context_management"):
        raise steering_error("steering_not_supported", "Steering cannot use conversations or automatic compaction.")
    reasoning = data.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("mode") not in (None, "standard"):
        raise steering_error("steering_not_supported", "Steering requires standard single-agent reasoning.")
    original_input = data.get("input")
    if isinstance(original_input, list):
        for item in original_input:
            if isinstance(item, dict) and item.get("type") == "configuration_update":
                update = item.get("reasoning")
                if isinstance(update, dict) and isinstance(update.get("effort"), str):
                    data["reasoning"] = {
                        **(reasoning if isinstance(reasoning, dict) else {}),
                        "effort": update["effort"],
                    }
    effective_reasoning = data.get("reasoning")
    if not isinstance(effective_reasoning, dict) or effective_reasoning.get("effort") is None:
        data["reasoning"] = {
            **(effective_reasoning if isinstance(effective_reasoning, dict) else {}),
            "effort": "medium",
        }
    data.pop("type", None)
    data["input"] = input_items
    data["previous_response_id"] = parent_id
    return ResponsesRequest.model_validate(data)


def continuation_for_created(
    payload: dict[str, JsonValue] | None, control: _WebSocketUpstreamControl
) -> _WebSocketSteeringContinuation | None:
    response = payload.get("response") if payload is not None else None
    parent_id = response.get("previous_response_id") if isinstance(response, dict) else None
    return control.steering_continuations.get(parent_id) if isinstance(parent_id, str) else None


def assign_websocket_created_request_state(
    payload: dict[str, JsonValue] | None,
    *,
    response_id: str | None,
    control: _WebSocketUpstreamControl,
    pending_requests: deque[_WebSocketRequestState],
) -> _WebSocketRequestState | None:
    continuation = continuation_for_created(payload, control)
    if continuation is None:
        return _assign_websocket_response_id(pending_requests, response_id)
    request_state = continuation.request_state
    if request_state not in pending_requests:
        if response_id is not None:
            control.suppressed_steering_response_ids.add(response_id)
        return None
    continuation.parent.steering_continuation_started = True
    request_state.response_id = response_id
    if request_state.request_text is not None:
        if request_state.steering_configuration is None:
            request_state.steering_configuration = json.loads(request_state.request_text)
        request_state.request_text = None
        request_state.fresh_upstream_request_text = None
        request_state.fresh_upstream_request_is_retry_safe = False
    control.steering_continuations.pop(request_state.steering_parent_response_id, None)
    return request_state


def steering_failure_payload(payload: Mapping[str, JsonValue], exc: ProxyResponseError) -> dict[str, JsonValue]:
    return {
        "type": "response.steer.failed",
        "steer": {
            "previous_response_id": payload.get("previous_response_id"),
            "input": payload.get("input"),
        },
        "error": cast(JsonValue, dict(exc.payload["error"])),
    }


def completed_steering_required_input(payload: dict[str, JsonValue] | None) -> list[JsonValue] | None:
    response = payload.get("response") if payload is not None else None
    output = response.get("output") if isinstance(response, dict) else None
    if not isinstance(output, list):
        return None
    required: list[JsonValue] = []
    for item in output:
        if not isinstance(item, dict) or item.get("async") is True:
            continue
        item_type = item.get("type")
        if item_type in {"function_call", "custom_tool_call"} and isinstance(item.get("call_id"), str):
            required.append({"type": f"{item_type}_output", "call_id": item["call_id"]})
        elif item_type == "mcp_approval_request" and isinstance(item.get("id"), str):
            required.append({"type": "mcp_approval_response", "approval_request_id": item["id"]})
    return required or None


def required_steering_input_is_present(required: list[JsonValue], input_items: JsonValue) -> bool:
    if not isinstance(input_items, list):
        return False
    for stub in required:
        if not isinstance(stub, dict):
            return False
        item_type = stub.get("type")
        identity_field = "approval_request_id" if item_type == "mcp_approval_response" else "call_id"
        identity = stub.get(identity_field)
        if not isinstance(identity, str):
            return False
        result_field = "approve" if item_type == "mcp_approval_response" else "output"
        if not any(
            isinstance(item, dict)
            and item.get("type") == item_type
            and item.get(identity_field) == identity
            and result_field in item
            for item in input_items
        ):
            return False
    return True


async def release_steering_request(proxy: _WebSocketServiceProtocol, request_state: _WebSocketRequestState) -> None:
    async def release() -> None:
        try:
            await _release_websocket_response_create_gate(request_state, asyncio.Semaphore(0))
        finally:
            await proxy._release_websocket_request_state_reservation(request_state)

    cancellation = await _await_cleanup_deferring_cancellation(release())
    if cancellation is not None:
        raise cancellation


async def submit_websocket_steering(
    proxy: _WebSocketServiceProtocol,
    payload: dict[str, JsonValue],
    *,
    headers: Mapping[str, str],
    account: Account,
    upstream: UpstreamWebSocket,
    control: _WebSocketUpstreamControl,
    pending_requests: deque[_WebSocketRequestState],
    pending_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    prohibit_fast_mode: bool,
) -> None:
    parent_id, input_items = validate_steering_input(payload)
    wire_text = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    wire_bytes = len(wire_text.encode("utf-8"))
    max_input_bytes = get_settings().upstream_response_create_max_bytes
    if wire_bytes > max_input_bytes:
        raise steering_error("payload_too_large", "Steering input exceeds the WebSocket payload limit.")
    async with pending_lock:
        parent = steering_parent(parent_id, pending_requests=pending_requests, control=control)
        initial_continuation = control.steering_continuations.get(parent_id)
        if initial_continuation is not None and initial_continuation.queued_input_bytes + wire_bytes > max_input_bytes:
            raise steering_error("payload_too_large", "Queued steering input exceeds the WebSocket payload limit.")
    if parent.replay_required_account_id not in (None, account.id):
        raise steering_error("response_not_found", "The response belongs to another upstream connection.")
    request = steering_response_payload(parent, parent_id=parent_id, input_items=input_items)
    inherited_request = request.model_copy(deep=True)
    normalize_unsupported_reasoning_effort(inherited_request)
    before_policy = inherited_request.to_payload()
    refreshed_key = await proxy._refresh_websocket_api_key_policy(api_key)
    if (
        refreshed_key is not None
        and refreshed_key.enforced_reasoning_effort is not None
        and request.reasoning is not None
        and refreshed_key.enforced_reasoning_effort != request.reasoning.effort
    ):
        raise steering_error("steering_not_supported", "Updated reasoning policy requires an explicit response.create.")
    apply_api_key_enforcement(request, refreshed_key, prohibit_fast_mode=prohibit_fast_mode)
    validate_model_access(refreshed_key, request.model)
    if request.to_payload() != before_policy:
        raise steering_error("steering_not_supported", "Updated request policy requires an explicit response.create.")
    proxy._raise_for_unsupported_input_image_references(request)
    file_owner = await proxy._resolve_file_account_for_responses(request, headers)
    if file_owner is not None and file_owner != account.id:
        raise steering_error("invalid_input", "Steering files must belong to the active response account.")
    selected_account, error_code, error_message = await proxy._revalidate_open_websocket_account(
        account,
        request_state=replace(parent, started_at=time.monotonic()),
        api_key=refreshed_key,
    )
    if selected_account is None or selected_account.id != account.id:
        raise steering_error(
            error_code or "no_available_accounts", error_message or "The response account is unavailable."
        )
    async with pending_lock:
        if steering_parent(parent_id, pending_requests=pending_requests, control=control) is not parent:
            raise steering_error("response_not_found", "The response is no longer available for steering.")
        continuation = control.steering_continuations.get(parent_id)
        if continuation is not initial_continuation:
            raise steering_error("response_not_found", "Steering ownership changed while validating the request.")
    request_usage_budget = estimate_api_key_request_usage(request)
    if continuation is None:
        reservation = await proxy._reserve_websocket_api_key_usage(
            refreshed_key,
            request_model=request.model,
            request_service_tier=request.service_tier,
            request_usage_budget=request_usage_budget,
        )
        child: _WebSocketRequestState | None = None
        registered = False
        try:
            child, _ = proxy._prepare_response_bridge_request_state(
                request,
                api_key=refreshed_key,
                api_key_reservation=reservation,
                include_type_field=True,
                attach_event_queue=False,
                transport=_REQUEST_TRANSPORT_WEBSOCKET,
                client_metadata=None,
                headers=headers,
                session_id=parent.session_id,
            )
            child.steering_parent_response_id = parent_id
            if child.steering_configuration is not None:
                child.steering_configuration.pop("input", None)
            child.request_text = None
            child.preferred_account_id = account.id
            child.replay_required_account_id = account.id
            child.require_security_work_authorized = parent.require_security_work_authorized
            child.durable_capability_lineage_required = parent.durable_capability_lineage_required
            child.useragent = parent.useragent
            child.useragent_group = parent.useragent_group
            child.conversation_id = parent.conversation_id
            child.client_ip = parent.client_ip
            child.responses_lite_model = parent.responses_lite_model
            child.input_item_count = 0
            child.input_full_fingerprint = None
            # Server scheduling does not occupy the client-create semaphore:
            # tool-result response.create must remain readable while queued.
            private_gate = asyncio.Semaphore(1)
            await proxy._acquire_request_state_response_create_admission(
                child, response_create_gate=private_gate, account_id=account.id, surface="websocket"
            )
            child.response_create_gate_acquired = False
            child.response_create_gate = None
            private_gate.release()
            child.awaiting_response_created = False
            continuation = _WebSocketSteeringContinuation(parent=parent, request_state=child)
            async with pending_lock:
                if (
                    control.reconnect_requested
                    or steering_parent(parent_id, pending_requests=pending_requests, control=control) is not parent
                    or control.steering_continuations.get(parent_id) is not initial_continuation
                ):
                    raise steering_error("response_not_found", "The upstream connection is no longer available.")
                pending_requests.append(child)
                control.steering_continuations[parent_id] = continuation
                registered = True
            proxy._start_request_state_api_key_reservation_heartbeat(child, api_key=refreshed_key, surface="websocket")
        except BaseException:
            if registered and child is not None:
                cleanup_state = False
                async with pending_lock:
                    if control.steering_continuations.get(parent_id) is continuation:
                        control.steering_continuations.pop(parent_id, None)
                    if child in pending_requests:
                        pending_requests.remove(child)
                        cleanup_state = True
                if cleanup_state:
                    await release_steering_request(proxy, child)
            else:
                if child is not None:
                    await release_steering_request(proxy, child)
                else:
                    await proxy._release_websocket_reservation(reservation)
            raise
    if parent.request_text is not None:
        if parent.steering_configuration is None:
            parent.steering_configuration = json.loads(parent.request_text)
        parent.request_text = None
        parent.fresh_upstream_request_text = None
        parent.fresh_upstream_request_is_retry_safe = False
    if initial_continuation is not None:
        reservation = continuation.request_state.api_key_reservation
        extended = await proxy._extend_websocket_api_key_usage(
            reservation,
            request_service_tier=request.service_tier,
            request_usage_budget=request_usage_budget,
        )
        if not extended:
            raise steering_error("response_not_found", "The steering continuation is no longer active.")
    submission = _WebSocketSteerSubmission(
        input=payload.get("input"),
        wire_bytes=wire_bytes,
        request_usage_budget=request_usage_budget,
        request_service_tier=request.service_tier,
    )
    try:
        async with pending_lock:
            if (
                control.steering_continuations.get(parent_id) is not continuation
                or continuation.request_state not in pending_requests
            ):
                raise steering_error("response_not_found", "The steering continuation is no longer active.")
            continuation.submissions.append(submission)
            continuation.queued_input_bytes += wire_bytes
    except BaseException:
        if initial_continuation is not None:
            await proxy._reduce_websocket_api_key_usage(
                reservation,
                request_service_tier=request.service_tier,
                request_usage_budget=request_usage_budget,
            )
        else:
            cleanup_state = False
            async with pending_lock:
                if control.steering_continuations.get(parent_id) is continuation:
                    control.steering_continuations.pop(parent_id, None)
                if continuation.request_state in pending_requests:
                    pending_requests.remove(continuation.request_state)
                    cleanup_state = True
            if cleanup_state:
                await release_steering_request(proxy, continuation.request_state)
        raise
    try:
        await upstream.send_text(wire_text)
    except BaseException:
        # Delivery is ambiguous. Let the connection's owned pending drain
        # settle/release every queued state; do not detach it before cleanup.
        control.reconnect_requested = True
        control.retire_after_drain = True
        with anyio.CancelScope(shield=True):
            await upstream.close()
        raise


async def process_websocket_steering_event(
    proxy: _WebSocketServiceProtocol,
    payload: dict[str, JsonValue],
    *,
    control: _WebSocketUpstreamControl,
    pending_requests: deque[_WebSocketRequestState],
    pending_lock: anyio.Lock,
) -> None:
    steer = payload.get("steer")
    if not isinstance(steer, dict):
        return
    parent_id = steer.get("previous_response_id")
    if not isinstance(parent_id, str):
        return
    release_state: _WebSocketRequestState | None = None
    reduce_submission: _WebSocketSteerSubmission | None = None
    reduce_reservation = None
    async with pending_lock:
        continuation = control.steering_continuations.get(parent_id)
        if continuation is None:
            return
        steer_id = steer.get("id")
        submission = next(
            (item for item in continuation.submissions if isinstance(steer_id, str) and item.id == steer_id), None
        )
        event_type = payload.get("type")
        if submission is None and event_type in {"response.steer.accepted", "response.steer.failed"}:
            submission = next(
                (
                    item
                    for item in continuation.submissions
                    if item.id is None and (event_type == "response.steer.accepted" or item.input == steer.get("input"))
                ),
                None,
            )
        if submission is None:
            return
        if event_type == "response.steer.accepted" and isinstance(steer_id, str):
            submission.id = steer_id
        elif event_type == "response.steer.pending":
            required = payload.get("required_input")
            if payload.get("reason") == "waiting_for_required_input" and isinstance(required, list):
                continuation.required_input = required
        elif event_type == "response.steer.failed":
            continuation.submissions.remove(submission)
            continuation.queued_input_bytes -= submission.wire_bytes
            if not continuation.submissions:
                control.steering_continuations.pop(parent_id, None)
                if continuation.explicit_request_prepared:
                    continuation.request_state.steering_parent_response_id = None
                else:
                    release_state = continuation.request_state
                    if release_state in pending_requests:
                        pending_requests.remove(release_state)
            elif not continuation.explicit_request_prepared:
                reduce_submission = submission
                reduce_reservation = continuation.request_state.api_key_reservation
    if release_state is not None:
        try:
            await release_steering_request(proxy, release_state)
        except Exception:
            # Match the queued-reduce path: a failed refund must not abort the
            # reader or fail unrelated in-flight responses on this socket.
            logger.exception(
                "Failed to release steering placeholder reservation request_id=%s",
                release_state.request_id,
            )
    elif reduce_submission is not None:
        await proxy._reduce_websocket_api_key_usage(
            reduce_reservation,
            request_service_tier=reduce_submission.request_service_tier,
            request_usage_budget=reduce_submission.request_usage_budget,
        )
