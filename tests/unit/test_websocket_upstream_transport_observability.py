from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from app.core.crypto import TokenEncryptor
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_HTTP,
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WebSocketRequestState,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket import mixin as websocket_mixin_module
from app.modules.proxy._service.websocket.mixin import _WebSocketMixin


class _DummyWebSocketService(_WebSocketMixin):
    def __init__(self) -> None:
        self.request_log_calls: list[dict[str, object]] = []
        self.remembered_response_ids: list[str] = []
        self._encryptor = TokenEncryptor()

        class _LoadBalancer:
            async def record_success(self, _account: object) -> None:
                return None

        class _ConnectLease:
            def release(self) -> None:
                return None

        class _WorkAdmission:
            async def acquire_websocket_connect(self) -> _ConnectLease:
                return _ConnectLease()

        self._load_balancer = _LoadBalancer()
        self._work_admission = _WorkAdmission()

    async def _write_request_log(self, **kwargs: object) -> None:
        self.request_log_calls.append(kwargs)

    def _cancel_request_state_api_key_reservation_heartbeat(self, _request_state: _WebSocketRequestState) -> None:
        return None

    async def _settle_stream_api_key_usage(self, *_args: object, **_kwargs: object) -> bool:
        return True

    async def _release_websocket_request_state_reservation(self, _request_state: _WebSocketRequestState) -> None:
        return None

    def _remember_websocket_previous_response_owner(
        self, *, previous_response_id: str | None, **_kwargs: object
    ) -> None:
        if previous_response_id is not None:
            self.remembered_response_ids.append(previous_response_id)

    def _get_work_admission(self) -> object:
        return self._work_admission

    async def _resolve_upstream_route_for_account(self, _account: object, *, operation: str) -> None:
        assert operation == "responses_websocket"
        return None


class _DummyFacade:
    _TRANSIENT_RETRY_CODES: frozenset[str] = frozenset()

    @staticmethod
    def _service_tier_from_event_payload(_payload: object) -> None:
        return None

    @staticmethod
    def _should_penalize_stream_error(_error_code: object) -> bool:
        return False

    @staticmethod
    def _maybe_dump_oversized_response_create_request(*_args: object, **_kwargs: object) -> None:
        return None


async def _no_op_release_gate(_request_state: object, _response_create_gate: object) -> None:
    return None


@pytest.fixture(autouse=True)
def _patch_websocket_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(websocket_mixin_module, "_facade", lambda: _DummyFacade())
    monkeypatch.setattr(websocket_mixin_module, "_release_websocket_response_create_gate", _no_op_release_gate)


@pytest.mark.asyncio
async def test_direct_websocket_connect_egress_normalizes_selected_installation_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _DummyWebSocketService()
    captured: dict[str, object] = {}
    expected_upstream = object()

    async def connect_responses_websocket(
        headers: dict[str, str],
        access_token: str,
        account_id: str | None,
        *,
        route: object,
        allow_direct_egress: bool,
    ) -> object:
        captured["headers"] = dict(headers)
        captured["access_token"] = access_token
        captured["account_id"] = account_id
        captured["route"] = route
        captured["allow_direct_egress"] = allow_direct_egress
        return expected_upstream

    class _DirectWebSocketFacade(_DummyFacade):
        connect_responses_websocket: Any

        @staticmethod
        async def _call_with_supported_optional_kwargs(
            function: object,
            *args: object,
            optional_kwargs: dict[str, object],
        ) -> object:
            return await cast(Any, function)(*args, **optional_kwargs)

    _DirectWebSocketFacade.connect_responses_websocket = staticmethod(connect_responses_websocket)
    monkeypatch.setattr(websocket_mixin_module, "_facade", lambda: _DirectWebSocketFacade())
    account = cast(
        Any,
        SimpleNamespace(
            access_token_encrypted=service._encryptor.encrypt("access-token"),
            chatgpt_account_id="account-123",
            codex_installation_id="account-installation",
        ),
    )

    upstream = await service._open_upstream_websocket(
        account,
        {
            "x-codex-installation-id": "client-installation",
            "x-codex-turn-metadata": '{"installation_id":"nested-client-installation","turn_id":"turn_123"}',
        },
    )

    assert upstream is expected_upstream
    assert captured["access_token"] == "access-token"
    assert captured["account_id"] == "account-123"
    assert captured["route"] is None
    assert captured["allow_direct_egress"] is True
    upstream_headers = cast(dict[str, str], captured["headers"])
    assert upstream_headers["x-codex-installation-id"] == "account-installation"
    assert json.loads(upstream_headers["x-codex-turn-metadata"]) == {
        "installation_id": "account-installation",
        "turn_id": "turn_123",
    }


@pytest.mark.asyncio
async def test_websocket_finalizer_records_bridge_upstream_transport_and_metric(monkeypatch):
    service = _DummyWebSocketService()
    metric_calls: list[dict[str, object]] = []

    def record_metric(**labels: object) -> None:
        metric_calls.append(dict(labels))

    monkeypatch.setattr(websocket_mixin_module, "_record_upstream_transport_decision", record_metric)

    request_state = _WebSocketRequestState(
        request_id="ws_bridge_success",
        request_log_id="resp_bridge_success_log",
        response_id="resp_bridge_success",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport=_REQUEST_TRANSPORT_HTTP,
        upstream_transport=_REQUEST_TRANSPORT_WEBSOCKET,
    )

    await service._finalize_websocket_request_state(
        request_state,
        account=cast(Any, object()),
        account_id_value="acc_bridge",
        event=None,
        event_type="response.completed",
        payload={},
        api_key=None,
        upstream_control=_WebSocketUpstreamControl(),
        response_create_gate=asyncio.Semaphore(1),
    )

    assert service.request_log_calls == [
        {
            "account_id": "acc_bridge",
            "api_key": None,
            "request_id": "resp_bridge_success",
            "archive_request_id": None,
            "model": "gpt-5.1",
            "latency_ms": service.request_log_calls[0]["latency_ms"],
            "status": "success",
            "error_code": None,
            "error_message": None,
            "failure_phase": None,
            "failure_detail": None,
            "upstream_error_code": None,
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "reasoning_effort": None,
            "transport": "http",
            "upstream_transport": "websocket",
            "service_tier": None,
            "requested_service_tier": None,
            "actual_service_tier": None,
            "latency_first_token_ms": None,
            "latency_response_created_ms": None,
            "latency_first_upstream_event_ms": None,
            "latency_response_create_gate_wait_ms": None,
            "latency_bridge_queue_wait_ms": None,
            "prewarm_status": None,
            "prewarm_latency_ms": None,
            "session_previous_gap_ms": None,
            "session_id": None,
            "upstream_proxy_route_mode": None,
            "upstream_proxy_pool_id": None,
            "upstream_proxy_endpoint_id": None,
            "upstream_proxy_fallback_used": None,
            "upstream_proxy_fail_closed_reason": None,
            "useragent": None,
            "useragent_group": None,
            "client_ip": None,
            "request_kind": "normal",
        }
    ]
    assert metric_calls == [
        {
            "downstream_transport": "http",
            "upstream_transport": "websocket",
            "policy": "bridge",
            "sticky": False,
            "status": "success",
        }
    ]


@pytest.mark.asyncio
async def test_websocket_connect_failure_records_bridge_upstream_transport_and_metric(monkeypatch):
    service = _DummyWebSocketService()
    metric_calls: list[dict[str, object]] = []

    def record_metric(**labels: object) -> None:
        metric_calls.append(dict(labels))

    monkeypatch.setattr(websocket_mixin_module, "_record_upstream_transport_decision", record_metric)

    request_state = _WebSocketRequestState(
        request_id="ws_bridge_failure",
        request_log_id="resp_bridge_failure",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport=_REQUEST_TRANSPORT_HTTP,
        upstream_transport=_REQUEST_TRANSPORT_WEBSOCKET,
    )

    await service._write_websocket_connect_failure(
        account_id="acc_bridge",
        api_key=None,
        request_state=request_state,
        error_code="upstream_unavailable",
        error_message="bridge upstream failed",
    )

    assert service.request_log_calls == [
        {
            "account_id": "acc_bridge",
            "api_key": None,
            "request_id": "resp_bridge_failure",
            "archive_request_id": None,
            "model": "gpt-5.1",
            "latency_ms": service.request_log_calls[0]["latency_ms"],
            "status": "error",
            "error_code": "upstream_unavailable",
            "error_message": "bridge upstream failed",
            "failure_phase": None,
            "failure_detail": None,
            "upstream_error_code": None,
            "reasoning_effort": None,
            "transport": "http",
            "upstream_transport": "websocket",
            "service_tier": None,
            "requested_service_tier": None,
            "actual_service_tier": None,
            "latency_first_token_ms": None,
            "latency_response_created_ms": None,
            "latency_first_upstream_event_ms": None,
            "latency_response_create_gate_wait_ms": None,
            "latency_bridge_queue_wait_ms": None,
            "prewarm_status": None,
            "prewarm_latency_ms": None,
            "session_previous_gap_ms": None,
            "session_id": None,
            "upstream_proxy_route_mode": None,
            "upstream_proxy_pool_id": None,
            "upstream_proxy_endpoint_id": None,
            "upstream_proxy_fallback_used": None,
            "upstream_proxy_fail_closed_reason": None,
            "useragent": None,
            "useragent_group": None,
            "client_ip": None,
            "request_kind": "normal",
        }
    ]


@pytest.mark.asyncio
async def test_fail_pending_websocket_requests_records_bridge_upstream_transport_and_metric(monkeypatch):
    service = _DummyWebSocketService()
    metric_calls: list[dict[str, object]] = []

    def record_metric(**labels: object) -> None:
        metric_calls.append(dict(labels))

    monkeypatch.setattr(websocket_mixin_module, "_record_upstream_transport_decision", record_metric)

    request_state = _WebSocketRequestState(
        request_id="ws_bridge_pending_failure",
        request_log_id="resp_bridge_pending_failure_log",
        response_id="resp_bridge_pending_failure",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        transport=_REQUEST_TRANSPORT_HTTP,
        upstream_transport=_REQUEST_TRANSPORT_WEBSOCKET,
    )

    await service._fail_pending_websocket_requests(
        account_id_value="acc_bridge",
        pending_requests=deque([request_state]),
        pending_lock=anyio.Lock(),
        error_code="stream_incomplete",
        error_message="Upstream websocket closed before response.completed",
        api_key=None,
        response_create_gate=asyncio.Semaphore(1),
    )

    assert service.request_log_calls == [
        {
            "account_id": "acc_bridge",
            "api_key": None,
            "request_id": "resp_bridge_pending_failure",
            "archive_request_id": None,
            "model": "gpt-5.1",
            "latency_ms": service.request_log_calls[0]["latency_ms"],
            "status": "error",
            "error_code": "stream_incomplete",
            "error_message": "Upstream websocket closed before response.completed",
            "failure_phase": None,
            "failure_detail": None,
            "upstream_error_code": None,
            "reasoning_effort": None,
            "transport": "http",
            "upstream_transport": "websocket",
            "service_tier": None,
            "requested_service_tier": None,
            "actual_service_tier": None,
            "latency_first_token_ms": None,
            "session_id": None,
            "upstream_proxy_route_mode": None,
            "upstream_proxy_pool_id": None,
            "upstream_proxy_endpoint_id": None,
            "upstream_proxy_fallback_used": None,
            "upstream_proxy_fail_closed_reason": None,
            "useragent": None,
            "useragent_group": None,
            "client_ip": None,
            "request_kind": "normal",
        }
    ]
    assert metric_calls == [
        {
            "downstream_transport": "http",
            "upstream_transport": "websocket",
            "policy": "bridge",
            "sticky": False,
            "status": "error",
        }
    ]
