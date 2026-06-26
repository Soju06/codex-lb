from __future__ import annotations

import time

import pytest

from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_HTTP,
    _REQUEST_TRANSPORT_WEBSOCKET,
    _WebSocketRequestState,
)
from app.modules.proxy._service.websocket import mixin as websocket_mixin_module
from app.modules.proxy._service.websocket.mixin import _WebSocketMixin


class _DummyWebSocketService(_WebSocketMixin):
    def __init__(self) -> None:
        self.request_log_calls: list[dict[str, object]] = []

    async def _write_request_log(self, **kwargs: object) -> None:
        self.request_log_calls.append(kwargs)


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
    assert metric_calls == [
        {
            "downstream_transport": "http",
            "upstream_transport": "websocket",
            "policy": "bridge",
            "sticky": False,
            "status": "error",
        }
    ]
