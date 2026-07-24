from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, cast

import pytest
from starlette.websockets import WebSocketState

import app.core.clients.proxy as core_proxy_module
import app.core.clients.proxy_websocket as proxy_websocket_module
import app.modules.proxy._service.realtime_live as realtime_live_module
import app.modules.proxy.service as proxy_service_module
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import (
    ArchivingResponsesWebSocket,
    UpstreamResponsesWebSocket,
    UpstreamWebSocketMessage,
)
from app.db.models import StickySessionKind
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.realtime_live import (
    _REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS,
    _REALTIME_CALL_AFFINITY_PREFIX,
    _RealtimeLiveMixin,
    _relay_live_websocket,
    normalize_realtime_call_id,
    realtime_call_affinity_key,
    realtime_call_id_from_location,
)
from app.modules.proxy.load_balancer import AccountLease, AccountSelection


class _FakeStickySessions:
    def __init__(self) -> None:
        self.purges: list[dict[str, Any]] = []
        self.insertions: list[tuple[str, str, Any]] = []
        self.persisted_owner_id: str | None = None
        self.account_id: str | None = None
        self.get_call: dict[str, Any] | None = None

    async def get_account_id(self, key: str, **kwargs: Any) -> str | None:
        self.get_call = {"key": key, **kwargs}
        return self.account_id

    async def purge_before_for_key_prefix(self, cutoff: Any, **kwargs: Any) -> int:
        self.purges.append({"cutoff": cutoff, **kwargs})
        return 0

    async def insert_if_absent(self, key: str, account_id: str, *, kind: Any) -> str:
        self.insertions.append((key, account_id, kind))
        return self.persisted_owner_id or account_id


class _FakeRepoContext:
    def __init__(self, sticky_sessions: _FakeStickySessions) -> None:
        self._repos = SimpleNamespace(sticky_sessions=sticky_sessions)

    async def __aenter__(self):
        return self._repos

    async def __aexit__(self, *_args) -> None:
        return None


class _BindingService(_RealtimeLiveMixin):
    def __init__(self, sticky_sessions: _FakeStickySessions) -> None:
        self._sticky_sessions = sticky_sessions

    def _repo_factory(self):
        return _FakeRepoContext(self._sticky_sessions)


class _FakeDownstreamWebSocket:
    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTING
        self.accepted = False
        self.close_codes: list[int] = []

    async def accept(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.accepted = True

    async def receive(self) -> dict[str, Any]:
        self.application_state = WebSocketState.DISCONNECTED
        return {"type": "websocket.disconnect", "code": 1000}

    async def send_text(self, _text: str) -> None:
        raise AssertionError("no upstream frame expected")

    async def send_bytes(self, _data: bytes) -> None:
        raise AssertionError("no upstream frame expected")

    async def close(self, *, code: int, reason: str = "") -> None:
        del reason
        self.close_codes.append(code)
        self.application_state = WebSocketState.DISCONNECTED


class _FakeUpstreamWebSocket:
    def __init__(self) -> None:
        self.closed = False
        self._wait_forever = asyncio.Event()

    async def send_text(self, _text: str) -> None:
        return None

    async def send_bytes(self, _data: bytes) -> None:
        return None

    async def receive(self):
        await self._wait_forever.wait()
        raise AssertionError("unreachable")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        del code, reason
        self.closed = True

    def response_header(self, _name: str) -> str | None:
        return None


class _FakeLoadBalancer:
    def __init__(self) -> None:
        self.released: list[object | None] = []

    async def release_account_lease(self, lease) -> None:
        self.released.append(lease)


class _ProxyService(_RealtimeLiveMixin):
    def __init__(self, account, lease, *, owner_account_id: str = "account-a") -> None:
        self.account = account
        self.owner_account_id = owner_account_id
        self.lease = lease
        self.selection_kwargs: dict[str, object] | None = None
        self._load_balancer = _FakeLoadBalancer()
        self.decrypt_calls: list[str] = []
        self._encryptor = SimpleNamespace(decrypt=self._decrypt)
        self.logs: list[dict[str, Any]] = []

    def _decrypt(self, value: str) -> str:
        self.decrypt_calls.append(value)
        return f"decrypted:{value}"

    async def _resolve_realtime_call_owner(self, call_id: str, *, api_key):
        assert call_id == "rtc_example"
        assert api_key is not None
        return self.owner_account_id

    async def _select_account_with_budget_compatible(self, _deadline: float, **kwargs):
        self.selection_kwargs = kwargs
        return AccountSelection(self.account, None, lease=self.lease)

    async def _resolve_upstream_route_for_account(self, account, *, operation: str):
        assert account is self.account
        assert operation == "realtime_live_websocket"
        return None

    async def _write_request_log(self, **kwargs) -> None:
        self.logs.append(kwargs)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rtc_example", "rtc_example"),
        (" rtc_example-2 ", "rtc_example-2"),
        ("123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
        ("call_example", None),
        ("rtc_", None),
        ("rtc_bad/value", None),
    ],
)
def test_normalize_realtime_call_id(value: str, expected: str | None) -> None:
    assert normalize_realtime_call_id(value) == expected


def test_realtime_call_id_from_relative_or_absolute_location() -> None:
    assert realtime_call_id_from_location({"Location": "/v1/live/rtc_relative"}) == "rtc_relative"
    assert (
        realtime_call_id_from_location({"location": "https://api.openai.com/v1/live/rtc_absolute?intent=quicksilver"})
        == "rtc_absolute"
    )
    assert realtime_call_id_from_location({"location": "/v1/realtime/calls/call_not_live"}) is None
    assert realtime_call_id_from_location({"location": "/unrelated/rtc_not_a_live_location"}) is None
    assert (
        realtime_call_id_from_location({"location": "/v1/realtime/calls/123e4567-e89b-12d3-a456-426614174000"})
        == "123e4567-e89b-12d3-a456-426614174000"
    )


@pytest.mark.asyncio
async def test_realtime_sdp_is_never_emitted_by_opt_in_payload_trace(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_sdp = b"v=0\r\na=ice-ufrag:secret-ice-credential\r\n"

    class Response:
        status = 201
        status_code = 201
        headers = {"content-type": "application/sdp", "location": "/v1/live/rtc_trace"}

        async def read(self) -> bytes:
            return b"v=answer\r\n"

    class RequestContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Session:
        def request(self, *_args, **_kwargs):
            return RequestContext()

    settings = core_proxy_module.get_settings().model_copy(update={"trace_channels": {"upstream_payload"}})
    monkeypatch.setattr(core_proxy_module, "get_settings", lambda: settings)

    with caplog.at_level(logging.DEBUG, logger="app.core.clients.proxy"):
        response = await core_proxy_module.codex_control_request(
            "realtime/calls",
            method="POST",
            payload=secret_sdp,
            query_params=[],
            headers={"content-type": "application/sdp"},
            access_token="account-token",
            account_id="account-a",
            session=cast(Any, Session()),
        )

    assert response.status_code == 201
    assert "secret-ice-credential" not in caplog.text
    assert all(getattr(record, "event", None) != "upstream_request_payload" for record in caplog.records)


def test_realtime_call_affinity_key_is_scoped_and_opaque() -> None:
    api_key_a = cast(ApiKeyData, SimpleNamespace(id="api-key-a"))
    api_key_b = cast(ApiKeyData, SimpleNamespace(id="api-key-b"))

    key_a = realtime_call_affinity_key("rtc_secret", api_key_a)
    key_b = realtime_call_affinity_key("rtc_secret", api_key_b)

    assert key_a.startswith(_REALTIME_CALL_AFFINITY_PREFIX)
    assert key_a != key_b
    assert "rtc_secret" not in key_a
    assert "api-key-a" not in key_a


@pytest.mark.asyncio
async def test_bind_realtime_call_owner_is_immutable_and_persists_only_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(realtime_live_module, "_realtime_call_cleanup_last_monotonic", 0.0)
    sticky_sessions = _FakeStickySessions()
    service = _BindingService(sticky_sessions)
    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-a"))

    call_id = await service.bind_realtime_call_owner(
        response_headers={"Location": "/v1/live/rtc_secret"},
        account_id="account-a",
        api_key=api_key,
    )

    assert call_id == "rtc_secret"
    assert len(sticky_sessions.purges) == 1
    assert sticky_sessions.purges[0]["key_prefix"] == _REALTIME_CALL_AFFINITY_PREFIX
    assert sticky_sessions.purges[0]["limit"] == 250
    assert len(sticky_sessions.insertions) == 1
    stored_key, stored_account_id, _kind = sticky_sessions.insertions[0]
    assert stored_account_id == "account-a"
    assert stored_key == realtime_call_affinity_key("rtc_secret", api_key)
    assert "rtc_secret" not in stored_key

    sticky_sessions.persisted_owner_id = "account-b"
    with pytest.raises(RuntimeError, match="already bound"):
        await service.bind_realtime_call_owner(
            response_headers={"Location": "/v1/live/rtc_secret"},
            account_id="account-a",
            api_key=api_key,
        )


@pytest.mark.asyncio
async def test_resolve_missing_realtime_call_is_read_only() -> None:
    sticky_sessions = _FakeStickySessions()
    service = _BindingService(sticky_sessions)
    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-a"))

    account_id = await service._resolve_realtime_call_owner("rtc_expired", api_key=api_key)

    affinity_key = realtime_call_affinity_key("rtc_expired", api_key)
    assert account_id is None
    assert sticky_sessions.get_call == {
        "key": affinity_key,
        "kind": StickySessionKind.CODEX_SESSION,
        "max_age_seconds": _REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS,
    }
    assert sticky_sessions.purges == []


@pytest.mark.asyncio
async def test_live_connector_uses_frameless_url_and_omits_responses_beta(monkeypatch) -> None:
    sentinel = cast(UpstreamResponsesWebSocket, object())
    captured: dict[str, Any] = {}

    async def fake_connect_upstream_websocket(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return sentinel

    monkeypatch.setattr(
        proxy_websocket_module,
        "_connect_upstream_websocket",
        fake_connect_upstream_websocket,
    )

    result = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        {"OpenAI-Alpha": "quicksilver=v2"},
        "access-token",
        "account-a",
        query_params=[("intent", "quicksilver"), ("architecture", "avas")],
    )

    assert result is sentinel
    assert captured["kwargs"]["url"] == "wss://api.openai.com/v1/live/rtc_example?intent=quicksilver&architecture=avas"
    assert captured["kwargs"]["include_responses_beta"] is False
    assert captured["kwargs"]["archive_payloads"] is False
    assert captured["kwargs"]["live_sideband"] is True
    assert captured["kwargs"]["operation"] == "live websocket"


@pytest.mark.asyncio
async def test_live_websocket_wrapper_never_archives_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_text: list[str] = []
    sent_bytes: list[bytes] = []

    class Wrapped:
        uses_proxy = False

        async def send_text(self, text: str) -> None:
            sent_text.append(text)

        async def send_bytes(self, data: bytes) -> None:
            sent_bytes.append(data)

        async def receive(self) -> UpstreamWebSocketMessage:
            return UpstreamWebSocketMessage(kind="close", close_code=1000)

        async def close(self, code: int = 1000, reason: str = "") -> None:
            del code, reason
            return None

        def response_header(self, _name: str) -> str | None:
            return None

    def fail_archive(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("live sideband frames must not be archived")

    monkeypatch.setattr(proxy_websocket_module, "archive_text", fail_archive)
    monkeypatch.setattr(proxy_websocket_module, "archive_bytes", fail_archive)
    websocket = ArchivingResponsesWebSocket(
        cast(UpstreamResponsesWebSocket, Wrapped()),
        url="wss://api.openai.com/v1/live/rtc_example",
        headers={},
        account_id="account-a",
        archive_payloads=False,
    )

    await websocket.send_text("event")
    await websocket.send_bytes(b"audio")
    websocket.archive_received(UpstreamWebSocketMessage(kind="text", text="response"))
    websocket.archive_received(UpstreamWebSocketMessage(kind="binary", data=b"response"))

    assert sent_text == ["event"]
    assert sent_bytes == [b"audio"]


@pytest.mark.asyncio
async def test_live_relay_forwards_downstream_text_and_binary_verbatim() -> None:
    class Downstream:
        def __init__(self) -> None:
            self.messages = [
                {"type": "websocket.receive", "text": "event"},
                {"type": "websocket.receive", "bytes": b"audio"},
                {"type": "websocket.disconnect", "code": 1001, "reason": "client done"},
            ]

        async def receive(self) -> dict[str, Any]:
            return self.messages.pop(0)

        async def send_text(self, _text: str) -> None:
            raise AssertionError("no upstream frame expected")

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("no upstream frame expected")

        async def close(self, *, code: int, reason: str = "") -> None:
            del code, reason

    class Upstream:
        def __init__(self) -> None:
            self.text: list[str] = []
            self.binary: list[bytes] = []
            self.close_frames: list[tuple[int, str]] = []
            self.wait = asyncio.Event()

        async def send_text(self, text: str) -> None:
            self.text.append(text)

        async def send_bytes(self, data: bytes) -> None:
            self.binary.append(data)

        async def receive(self) -> UpstreamWebSocketMessage:
            await self.wait.wait()
            raise AssertionError("unreachable")

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.close_frames.append((code, reason))

    upstream = Upstream()
    await _relay_live_websocket(
        cast(Any, Downstream()),
        cast(UpstreamResponsesWebSocket, upstream),
        max_message_bytes=1024,
    )

    assert upstream.text == ["event"]
    assert upstream.binary == [b"audio"]
    assert upstream.close_frames == [(1001, "client done")]


@pytest.mark.asyncio
async def test_live_relay_forwards_upstream_frames_and_close_code() -> None:
    class Downstream:
        def __init__(self) -> None:
            self.text: list[str] = []
            self.binary: list[bytes] = []
            self.close_codes: list[int] = []
            self.close_reasons: list[str] = []
            self.wait = asyncio.Event()
            self.application_state = WebSocketState.CONNECTED

        async def receive(self) -> dict[str, Any]:
            await self.wait.wait()
            raise AssertionError("unreachable")

        async def send_text(self, text: str) -> None:
            self.text.append(text)

        async def send_bytes(self, data: bytes) -> None:
            self.binary.append(data)

        async def close(self, *, code: int, reason: str = "") -> None:
            self.close_codes.append(code)
            self.close_reasons.append(reason)

    class Upstream:
        def __init__(self) -> None:
            self.messages = [
                UpstreamWebSocketMessage(kind="text", text="event"),
                UpstreamWebSocketMessage(kind="binary", data=b"audio"),
                UpstreamWebSocketMessage(kind="close", close_code=1001, close_reason="server done"),
            ]
            self.archived: list[UpstreamWebSocketMessage] = []

        async def send_text(self, _text: str) -> None:
            raise AssertionError("no downstream frame expected")

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("no downstream frame expected")

        async def receive(self) -> UpstreamWebSocketMessage:
            return self.messages.pop(0)

        def archive_received(self, message: UpstreamWebSocketMessage) -> None:
            self.archived.append(message)

    downstream = Downstream()
    upstream = Upstream()
    await _relay_live_websocket(
        cast(Any, downstream),
        cast(UpstreamResponsesWebSocket, upstream),
        max_message_bytes=1024,
    )

    assert downstream.text == ["event"]
    assert downstream.binary == [b"audio"]
    assert downstream.close_codes == [1001]
    assert downstream.close_reasons == ["server done"]
    assert [message.kind for message in upstream.archived] == ["text", "binary", "close"]


@pytest.mark.asyncio
async def test_live_relay_cancellation_stops_both_direction_tasks() -> None:
    downstream_started = asyncio.Event()
    downstream_stopped = asyncio.Event()
    upstream_started = asyncio.Event()
    upstream_stopped = asyncio.Event()

    class Downstream:
        application_state = WebSocketState.CONNECTED

        async def receive(self) -> dict[str, object]:
            downstream_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                downstream_stopped.set()
            raise AssertionError("unreachable")

        async def send_text(self, _text: str) -> None:
            raise AssertionError("unexpected text")

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("unexpected bytes")

        async def close(self, *, code: int, reason: str = "") -> None:
            del code, reason

    class Upstream:
        async def send_text(self, _text: str) -> None:
            raise AssertionError("unexpected text")

        async def send_bytes(self, _data: bytes) -> None:
            raise AssertionError("unexpected bytes")

        async def receive(self) -> UpstreamWebSocketMessage:
            upstream_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                upstream_stopped.set()
            raise AssertionError("unreachable")

        async def close(self, code: int = 1000, reason: str = "") -> None:
            del code, reason
            return None

        def archive_received(self, _message: UpstreamWebSocketMessage) -> None:
            return None

    task = asyncio.create_task(
        _relay_live_websocket(
            cast(Any, Downstream()),
            cast(UpstreamResponsesWebSocket, Upstream()),
            max_message_bytes=1024,
        )
    )
    await asyncio.wait_for(asyncio.gather(downstream_started.wait(), upstream_started.wait()), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert downstream_stopped.is_set()
    assert upstream_stopped.is_set()


def test_live_header_builder_replaces_identity_and_preserves_frameless_metadata() -> None:
    headers = proxy_websocket_module._build_upstream_live_websocket_headers(
        {
            "Authorization": "Bearer codex-lb-key",
            "ChatGPT-Account-ID": "wrong-account",
            "User-Agent": "codex_cli_rs/1.0",
            "OpenAI-Alpha": "quicksilver=v2",
            "OpenAI-Beta": "realtime=v1, responses=experimental, responses_websockets=2026-02-06",
            "x-oai-attestation": "attestation",
            "x-session-id": "session-a",
            "session-id": "session-b",
            "thread-id": "thread-a",
            "originator": "codex_cli_rs",
            "Sec-WebSocket-Key": "must-not-forward",
        },
        "account-token",
        "account-a",
    )
    lowered = {key.lower(): value for key, value in headers.items()}

    assert lowered["authorization"] == "Bearer account-token"
    assert lowered["chatgpt-account-id"] == "account-a"
    assert lowered["openai-alpha"] == "quicksilver=v2"
    assert lowered["x-oai-attestation"] == "attestation"
    assert lowered["x-session-id"] == "session-a"
    assert lowered["session-id"] == "session-b"
    assert lowered["thread-id"] == "thread-a"
    assert lowered["originator"] == "codex_cli_rs"
    assert lowered["openai-beta"] == "realtime=v1"
    assert "sec-websocket-key" not in lowered
    assert "codex-lb-key" not in str(headers)

    responses_only = proxy_websocket_module._build_upstream_live_websocket_headers(
        {"OpenAI-Beta": "responses=experimental, responses_websockets=2026-02-06"},
        "account-token",
        "account-a",
    )
    assert "openai-beta" not in {key.lower() for key in responses_only}


@pytest.mark.asyncio
async def test_proxy_live_sideband_uses_exact_owner_without_refresh_or_failover(monkeypatch) -> None:
    lease = cast(AccountLease, object())
    account = SimpleNamespace(
        id="account-a",
        access_token_encrypted="encrypted-token",
        chatgpt_account_id="chatgpt-account-a",
        codex_installation_id="installation-a",
    )
    service = _ProxyService(account, lease)
    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-a"))
    downstream = _FakeDownstreamWebSocket()
    upstream = _FakeUpstreamWebSocket()
    connector_calls: list[dict[str, Any]] = []

    async def fake_connect_live_websocket(call_id, headers, access_token, account_id, **kwargs):
        connector_calls.append(
            {
                "call_id": call_id,
                "headers": headers,
                "access_token": access_token,
                "account_id": account_id,
                "kwargs": kwargs,
            }
        )
        return upstream

    monkeypatch.setattr(proxy_service_module, "connect_live_websocket", fake_connect_live_websocket)

    await service.proxy_realtime_live_websocket(
        cast(Any, downstream),
        "rtc_example",
        {
            "OpenAI-Alpha": "quicksilver=v2",
            "x-oai-attestation": "attestation",
            "X-Codex-Installation-Id": "client-controlled-installation",
        },
        [("intent", "quicksilver"), ("architecture", "avas")],
        api_key=api_key,
    )

    assert downstream.accepted is True
    assert upstream.closed is True
    assert service.selection_kwargs is not None
    assert service.selection_kwargs["preferred_account_id"] == "account-a"
    assert service.selection_kwargs["preferred_account_is_continuity_owner"] is True
    assert service.selection_kwargs["fallback_on_preferred_account_unavailable"] is False
    assert service.selection_kwargs["lease_kind"] == "stream"
    assert service.selection_kwargs["request_stage"] == "reattach"
    assert connector_calls == [
        {
            "call_id": "rtc_example",
            "headers": {
                "OpenAI-Alpha": "quicksilver=v2",
                "x-oai-attestation": "attestation",
                "x-codex-installation-id": "installation-a",
            },
            "access_token": "decrypted:encrypted-token",
            "account_id": "chatgpt-account-a",
            "kwargs": {
                "route": None,
                "allow_direct_egress": True,
                "query_params": [("intent", "quicksilver"), ("architecture", "avas")],
            },
        }
    ]
    assert service._load_balancer.released == [lease]
    assert service.logs[-1]["status"] == "success"
    assert service.logs[-1]["account_id"] == "account-a"
    assert service.logs[-1]["model"] is None
    assert service.logs[-1]["request_kind"] == "realtime_live"
    assert _REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS == 2 * 60 * 60


@pytest.mark.asyncio
async def test_live_sideband_unavailable_exact_owner_never_falls_back_or_decrypts() -> None:
    service = _ProxyService(None, None)
    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-a"))
    downstream = _FakeDownstreamWebSocket()

    with pytest.raises(ProxyResponseError) as raised:
        await service.proxy_realtime_live_websocket(
            cast(Any, downstream),
            "rtc_example",
            {},
            api_key=api_key,
        )

    assert raised.value.status_code == 503
    assert raised.value.payload["error"]["code"] == "continuity_owner_unavailable"
    assert downstream.accepted is False
    assert service.decrypt_calls == []
    assert service._load_balancer.released == [None]
    assert service.selection_kwargs is not None
    assert service.selection_kwargs["preferred_account_id"] == "account-a"
    assert service.selection_kwargs["fallback_on_preferred_account_unavailable"] is False
