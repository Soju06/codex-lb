from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace

import aiohttp
import pytest

from app.core.config.settings import get_settings
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy.http_bridge_forwarding import (
    HTTP_BRIDGE_AFFINITY_KEY_HEADER,
    HTTP_BRIDGE_AFFINITY_KIND_HEADER,
    HTTP_BRIDGE_CODEX_AFFINITY_HEADER,
    HTTP_BRIDGE_FORWARDED_HEADER,
    HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER,
    HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER,
    HTTP_BRIDGE_RESERVATION_MODEL_HEADER,
    HTTP_BRIDGE_SIGNATURE_HEADER,
    HTTP_BRIDGE_TARGET_INSTANCE_HEADER,
    HTTPBridgeForwardContext,
    HTTPBridgeOwnerClient,
    _owner_forward_receive_timeout,
    _owner_forward_timeout,
    build_owner_forward_headers,
    parse_forwarded_request,
)


@pytest.fixture(autouse=True)
def _temp_bridge_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CODEX_LB_ENCRYPTION_KEY_FILE", str(tmp_path / "bridge.key"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _payload() -> ResponsesRequest:
    return ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})


def test_parse_forwarded_request_accepts_signed_internal_forward() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=True,
        downstream_turn_state="http_turn_123",
        reservation=ApiKeyUsageReservationData(
            reservation_id="res_123",
            key_id="key_123",
            model="gpt-5.4",
        ),
    )
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)

    forwarded, error = parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="instance-b",
    )

    assert error is None
    assert forwarded is not None
    assert forwarded.context == context
    assert forwarded.context.original_affinity_kind is None
    assert forwarded.context.original_affinity_key is None


def test_build_owner_forward_headers_preserves_original_affinity_key() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=True,
        downstream_turn_state="http_turn_123",
        original_affinity_kind="session_header",
        original_affinity_key="sid-123",
    )

    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)

    assert headers[HTTP_BRIDGE_AFFINITY_KIND_HEADER] == "session_header"
    assert headers[HTTP_BRIDGE_AFFINITY_KEY_HEADER] == "sid-123"


def test_build_owner_forward_headers_strips_transport_and_bridge_headers() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=True,
        downstream_turn_state="http_turn_123",
    )

    headers = build_owner_forward_headers(
        headers={
            "Host": "127.0.0.1:2455",
            "Connection": "keep-alive, X-Remove-Me",
            "Keep-Alive": "timeout=5",
            "Transfer-Encoding": "chunked",
            "Content-Length": "42",
            "Content-Encoding": "gzip",
            "X-Remove-Me": "connection-token",
            "x-codex-bridge-target-instance": "spoofed",
            "Content-Type": "application/json",
            "Authorization": "Bearer proxy-key",
        },
        payload=payload,
        context=context,
    )

    lowered = {key.lower() for key in headers}
    assert "host" not in lowered
    assert "connection" not in lowered
    assert "keep-alive" not in lowered
    assert "transfer-encoding" not in lowered
    assert "content-length" not in lowered
    assert "content-encoding" not in lowered
    assert "x-remove-me" not in lowered
    assert headers[HTTP_BRIDGE_TARGET_INSTANCE_HEADER] == "instance-b"
    assert headers["Content-Type"] == "application/json"
    assert headers["Authorization"] == "Bearer proxy-key"


def test_parse_forwarded_request_rejects_missing_signature() -> None:
    payload = _payload()
    headers = {
        HTTP_BRIDGE_FORWARDED_HEADER: "1",
        HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER: "instance-a",
        HTTP_BRIDGE_TARGET_INSTANCE_HEADER: "instance-b",
        HTTP_BRIDGE_CODEX_AFFINITY_HEADER: "0",
    }

    forwarded, error = parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="instance-b",
    )

    assert forwarded is None
    assert error is not None
    assert error.status_code == 400
    assert error.payload["error"]["code"] == "bridge_forward_invalid"


def test_parse_forwarded_request_rejects_tampered_signature() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=False,
        downstream_turn_state=None,
        reservation=ApiKeyUsageReservationData(
            reservation_id="res_123",
            key_id="key_123",
            model="gpt-5.4",
        ),
    )
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers[HTTP_BRIDGE_SIGNATURE_HEADER] = "bad-signature"

    forwarded, error = parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="instance-b",
    )

    assert forwarded is None
    assert error is not None
    assert error.status_code == 400
    assert error.payload["error"]["code"] == "bridge_forward_invalid"


def test_parse_forwarded_request_rejects_tampered_reservation_fields() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=False,
        downstream_turn_state=None,
        reservation=ApiKeyUsageReservationData(
            reservation_id="res_123",
            key_id="key_123",
            model="gpt-5.4",
        ),
    )
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers[HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER] = "key_tampered"
    headers[HTTP_BRIDGE_RESERVATION_MODEL_HEADER] = "gpt-5.5"

    forwarded, error = parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="instance-b",
    )

    assert forwarded is None
    assert error is not None
    assert error.status_code == 400
    assert error.payload["error"]["code"] == "bridge_forward_invalid"


def test_parse_forwarded_request_rejects_wrong_target_as_server_error() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=False,
        downstream_turn_state=None,
    )
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)

    forwarded, error = parse_forwarded_request(
        headers,
        payload=payload,
        current_instance="instance-c",
    )

    assert forwarded is None
    assert error is not None
    assert error.status_code == 503
    assert error.payload["error"]["code"] == "bridge_owner_forward_failed"


def test_owner_forward_timeout_only_bounds_connect_phase() -> None:
    timeout = _owner_forward_timeout(connect_timeout_seconds=8.0, idle_timeout_seconds=300.0)

    assert timeout.total is None
    assert timeout.sock_connect == pytest.approx(8.0)
    assert timeout.sock_read == pytest.approx(300.0)


def test_owner_forward_receive_timeout_prefers_idle_timeout_with_budget_remaining(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.modules.proxy.http_bridge_forwarding.time.monotonic", lambda: 100.0)

    timeout = _owner_forward_receive_timeout(
        request_started_at=10.0,
        proxy_request_budget_seconds=300.0,
        stream_idle_timeout_seconds=45.0,
    )

    assert timeout.timeout_seconds == pytest.approx(45.0)
    assert timeout.error_code == "stream_idle_timeout"


def test_owner_forward_receive_timeout_clamps_to_remaining_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.modules.proxy.http_bridge_forwarding.time.monotonic", lambda: 100.0)

    timeout = _owner_forward_receive_timeout(
        request_started_at=10.0,
        proxy_request_budget_seconds=95.0,
        stream_idle_timeout_seconds=45.0,
    )

    assert timeout.timeout_seconds == pytest.approx(5.0)
    assert timeout.error_code == "upstream_request_timeout"


@pytest.mark.asyncio
async def test_owner_forward_uses_direct_session_without_env_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self) -> "FakeResponse":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        async def text(self) -> str:
            return ""

        @property
        def content(self) -> SimpleNamespace:
            async def _iter_chunked(_: int) -> AsyncIterator[bytes]:
                if False:
                    yield b""
                return

            return SimpleNamespace(iter_chunked=_iter_chunked)

    class FakeSession:
        def __init__(self, *, timeout: aiohttp.ClientTimeout, trust_env: bool) -> None:
            captured["timeout"] = timeout
            captured["trust_env"] = trust_env

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def post(self, url: str, **kwargs: object) -> FakeResponse:
            captured["url"] = url
            captured["headers"] = kwargs.get("headers")
            return FakeResponse()

    monkeypatch.setattr("app.modules.proxy.http_bridge_forwarding.aiohttp.ClientSession", FakeSession)
    monkeypatch.setattr("app.modules.proxy.http_bridge_forwarding.time.monotonic", lambda: 10.0)
    monkeypatch.setenv("CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("CODEX_LB_STREAM_IDLE_TIMEOUT_SECONDS", "11")
    get_settings.cache_clear()

    client = HTTPBridgeOwnerClient()
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=False,
        downstream_turn_state=None,
    )

    events = [
        event
        async for event in client.stream_responses(
            owner_endpoint="http://instance-b:2455",
            payload=payload,
            headers={"Authorization": "Bearer proxy-key"},
            context=context,
            request_started_at=10.0,
            proxy_request_budget_seconds=75.0,
        )
    ]

    assert events == []
    assert captured["trust_env"] is False
