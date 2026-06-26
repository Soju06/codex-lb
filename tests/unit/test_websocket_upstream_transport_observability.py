from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any, cast

import anyio
import pytest

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

        class _LoadBalancer:
            async def record_success(self, _account: object) -> None:
                return None

        self._load_balancer = _LoadBalancer()

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
            "model": "gpt-5.1",
            "latency_ms": service.request_log_calls[0]["latency_ms"],
            "status": "success",
            "error_code": None,
            "error_message": None,
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
            "session_id": None,
            "upstream_proxy_route_mode": None,
            "upstream_proxy_pool_id": None,
            "upstream_proxy_endpoint_id": None,
            "upstream_proxy_fallback_used": None,
            "upstream_proxy_fail_closed_reason": None,
            "useragent": None,
            "useragent_group": None,
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
            "model": "gpt-5.1",
            "latency_ms": service.request_log_calls[0]["latency_ms"],
            "status": "error",
            "error_code": "upstream_unavailable",
            "error_message": "bridge upstream failed",
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
            "model": "gpt-5.1",
            "latency_ms": service.request_log_calls[0]["latency_ms"],
            "status": "error",
            "error_code": "stream_incomplete",
            "error_message": "Upstream websocket closed before response.completed",
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
