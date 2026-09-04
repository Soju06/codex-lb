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
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import (
    UpstreamWebSocket,
    UpstreamWebSocketMessage,
    normalize_realtime_call_id,
)
from app.db.models import AccountStatus
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy._service.realtime_live import (
    _RealtimeLiveMixin,
    _relay_live_websocket,
    realtime_call_id_from_location,
)
from app.modules.proxy.load_balancer import AccountLease, AccountSelection


def _unscoped_api_key(*, key_id: str = "api-key-a") -> ApiKeyData:
    return cast(
        ApiKeyData,
        SimpleNamespace(
            id=key_id,
            account_assignment_scope_enabled=False,
            assigned_account_ids=(),
        ),
    )


class _FakeDownstreamWebSocket:
    def __init__(self, *, subprotocols: tuple[str, ...] = ()) -> None:
        self.scope = {"subprotocols": list(subprotocols)}
        self.application_state = WebSocketState.CONNECTING
        self.accepted = False
        self.accepted_subprotocol: str | None = None
        self.close_codes: list[int] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.accepted = True
        self.accepted_subprotocol = subprotocol

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
    def __init__(self, *, selected_subprotocol: str | None = None) -> None:
        self.close_calls: list[tuple[int, str]] = []
        self._wait_forever = asyncio.Event()
        self.selected_subprotocol = selected_subprotocol

    async def send_text(self, text: str) -> None:
        del text

    async def send_bytes(self, data: bytes) -> None:
        del data

    async def receive(self) -> UpstreamWebSocketMessage:
        await self._wait_forever.wait()
        raise AssertionError("unreachable")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append((code, reason))

    def response_header(self, name: str) -> str | None:
        if name.lower() == "sec-websocket-protocol":
            return self.selected_subprotocol
        return None


@pytest.mark.asyncio
async def test_close_once_live_websocket_cancels_and_awaits_timed_out_close() -> None:
    class HangingCloseUpstream(_FakeUpstreamWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.close_cancelled = asyncio.Event()

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.close_calls.append((code, reason))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.close_cancelled.set()
                raise

    upstream = HangingCloseUpstream()
    wrapped = realtime_live_module._CloseOnceLiveWebSocket(upstream)

    with pytest.raises(TimeoutError):
        await wrapped.close(timeout_seconds=0.01)

    assert upstream.close_cancelled.is_set()
    await wrapped.close(timeout_seconds=0.01)
    assert upstream.close_calls == [(1000, "")]


@pytest.mark.asyncio
async def test_close_once_live_websocket_bounds_cancellation_resistant_cleanup() -> None:
    class CancellationResistantCloseUpstream(_FakeUpstreamWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.close_cancelled = asyncio.Event()
            self.cleanup_release = asyncio.Event()
            self.cleanup_finished = asyncio.Event()

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.close_calls.append((code, reason))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.close_cancelled.set()
                await self.cleanup_release.wait()
                self.cleanup_finished.set()

    upstream = CancellationResistantCloseUpstream()
    wrapped = realtime_live_module._CloseOnceLiveWebSocket(upstream)

    with pytest.raises(TimeoutError):
        await wrapped.close(timeout_seconds=0.01)

    assert upstream.close_cancelled.is_set()
    assert not upstream.cleanup_finished.is_set()
    try:
        await asyncio.wait_for(wrapped.close(timeout_seconds=1), timeout=0.1)
    finally:
        upstream.cleanup_release.set()
    await asyncio.wait_for(upstream.cleanup_finished.wait(), timeout=1)
    assert upstream.close_calls == [(1000, "")]


class _FakeLoadBalancer:
    def __init__(self) -> None:
        self.released: list[object | None] = []

    async def release_account_lease(self, lease) -> None:
        self.released.append(lease)


class _FakeAccountsRepository:
    def __init__(self, account) -> None:
        self.account = account
        self.fresh_reads: list[str] = []
        self.session = SimpleNamespace(expunge_all=lambda: None)

    async def get_by_id_fresh(self, account_id: str):
        self.fresh_reads.append(account_id)
        return self.account


class _FakeProxyRepoContext:
    def __init__(self, accounts: _FakeAccountsRepository) -> None:
        self._repos = SimpleNamespace(accounts=accounts)

    async def __aenter__(self):
        return self._repos

    async def __aexit__(self, *_args) -> None:
        return None


class _ProxyService(_RealtimeLiveMixin):
    def __init__(
        self,
        account,
        lease,
        *,
        owner_account_id: str = "account-a",
        current_account=None,
        live_websocket_connector=None,
    ) -> None:
        self.account = account
        self.current_account = account if current_account is None else current_account
        self.accounts = _FakeAccountsRepository(self.current_account)
        self.owner_account_id = owner_account_id
        self.lease = lease
        self._load_balancer = _FakeLoadBalancer()
        self.selection_calls: list[dict[str, object]] = []
        self.decrypt_calls: list[str] = []
        self._encryptor = SimpleNamespace(decrypt=self._decrypt)
        self._live_websocket_connector = live_websocket_connector

    def _repo_factory(self):
        return _FakeProxyRepoContext(self.accounts)

    def _decrypt(self, value: str) -> str:
        self.decrypt_calls.append(value)
        return f"decrypted:{value}"

    async def _resolve_realtime_call_owner(self, call_id: str, *, api_key):
        assert call_id == "rtc_example"
        assert api_key is not None
        return self.owner_account_id

    async def _select_account_with_budget_compatible(self, _deadline: float, **_kwargs):
        self.selection_calls.append(_kwargs)
        return AccountSelection(self.account, None, lease=self.lease)

    async def _resolve_upstream_route_for_account(self, account, *, operation: str):
        assert account is self.current_account
        assert operation == "realtime_live_websocket"
        return None

    async def _write_request_log(self, **_kwargs) -> None:
        return None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("rtc_example", "rtc_example"),
        (" rtc_example-2 ", "rtc_example-2"),
        ("rtc_example.with_dot~and-hyphen", "rtc_example.with_dot~and-hyphen"),
        ("123e4567-e89b-12d3-a456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
        ("123E4567-E89B-12D3-A456-426614174000", "123e4567-e89b-12d3-a456-426614174000"),
        ("call_example", None),
        ("rtc_", None),
        ("rtc_bad/value", None),
        ("rtc_bad$value", None),
        ("rtc_" + ("a" * 253), None),
        ("123e4567e89b12d3a456426614174000", None),
        ("not-a-live-id", None),
    ],
)
def test_normalize_realtime_call_id(value: str, expected: str | None) -> None:
    assert normalize_realtime_call_id(value) == expected


def test_realtime_call_id_from_exact_relative_or_absolute_location() -> None:
    assert realtime_call_id_from_location({"Location": "/v1/realtime/calls/rtc_relative"}) == "rtc_relative"
    assert (
        realtime_call_id_from_location({"location": "https://api.openai.com/v1/realtime/calls/rtc_absolute"})
        == "rtc_absolute"
    )
    assert realtime_call_id_from_location({"location": "/v1/realtime/calls/call_not_live"}) is None
    assert realtime_call_id_from_location({"location": "/unrelated/rtc_not_a_live_location"}) is None
    assert (
        realtime_call_id_from_location({"location": "/v1/realtime/calls/123e4567-e89b-12d3-a456-426614174000"})
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert (
        realtime_call_id_from_location(
            {"location": "https://api.openai.com/v1/realtime/calls/123E4567-E89B-12D3-A456-426614174000"}
        )
        == "123e4567-e89b-12d3-a456-426614174000"
    )
    assert (
        realtime_call_id_from_location(
            {"location": "/v1/realtime/calls/rtc_query?intent=quicksilver&token=private-query"}
        )
        == "rtc_query"
    )
    assert (
        realtime_call_id_from_location(
            {"location": ("https://api.openai.com/v1/realtime/calls/rtc_fragment?intent=quicksilver#opaque-fragment")}
        )
        == "rtc_fragment"
    )


@pytest.mark.parametrize(
    "location",
    [
        "live/rtc_unsupported",
        "/unrelated/live/rtc_unsupported",
        "realtime/calls/rtc_unsupported",
        "/unrelated/realtime/calls/rtc_unsupported",
        "//attacker.invalid/v1/realtime/calls/rtc_unsupported",
        "///v1/realtime/calls/rtc_unsupported",
        "v1/realtime/calls/rtc_unsupported",
        "/v1/realtime/calls/rtc_unsupported#fragment",
        "/v1/realtime/calls/rtc_unsupported/extra",
        "ftp://api.openai.com/v1/realtime/calls/rtc_unsupported",
        "https:///v1/realtime/calls/rtc_unsupported",
        "https://api.openai.com/v1/realtime/calls/rtc_unsupported;param",
    ],
)
def test_realtime_call_id_rejects_unsupported_location_paths(location: str) -> None:
    assert realtime_call_id_from_location({"Location": location}) is None


@pytest.mark.asyncio
async def test_realtime_call_core_trace_is_content_free_on_success(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_sdp = b"v=0\r\na=ice-ufrag:secret-ice-credential\r\n"
    account_id = "private-account-identifier"
    access_token = "private-account-token"

    class Response:
        status = 201
        status_code = 201
        headers = {"content-type": "application/sdp", "location": "/v1/realtime/calls/rtc_trace"}

        async def read(self) -> bytes:
            return b"v=answer\r\na=ice-pwd:secret-answer-credential\r\n"

    class RequestContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Session:
        def request(self, *_args, **_kwargs):
            return RequestContext()

    settings = core_proxy_module.get_settings().model_copy(
        update={"trace_channels": {"upstream_summary", "upstream_payload"}}
    )
    monkeypatch.setattr(core_proxy_module, "get_settings", lambda: settings)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.core.clients.proxy"):
        response = await core_proxy_module.codex_control_request(
            "realtime/calls",
            method="POST",
            payload=secret_sdp,
            query_params=[],
            headers={"content-type": "application/sdp"},
            access_token=access_token,
            account_id=account_id,
            session=cast(Any, Session()),
        )

    assert response.status_code == 201
    assert "upstream_request_start" in caplog.text
    assert "upstream_request_complete" in caplog.text
    assert "account_id=<redacted>" in caplog.text
    assert all(getattr(record, "event", None) != "upstream_request_payload" for record in caplog.records)
    for secret in (
        account_id,
        access_token,
        "secret-ice-credential",
        "secret-answer-credential",
        "rtc_trace",
    ):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_realtime_call_core_trace_redacts_upstream_failure_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    account_id = "private-failure-account"
    access_token = "private-failure-token"
    raw_error_code = "malicious-upstream-code"
    raw_error_message = "malicious upstream body with private-failure-token"

    class Response:
        status = 403
        status_code = 403
        reason = "malicious forbidden reason"
        headers = {"content-type": "application/json", "x-private-header": "private-header-value"}

        async def read(self) -> bytes:
            return (
                b'{"error":{"code":"malicious-upstream-code","message":'
                b'"malicious upstream body with private-failure-token","type":"permission_error"}}'
            )

    class RequestContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, *_args):
            return None

    class Session:
        def request(self, *_args, **_kwargs):
            return RequestContext()

    settings = core_proxy_module.get_settings().model_copy(
        update={"trace_channels": {"upstream_summary", "upstream_payload"}}
    )
    monkeypatch.setattr(core_proxy_module, "get_settings", lambda: settings)

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.core.clients.proxy"):
        with pytest.raises(ProxyResponseError) as raised:
            await core_proxy_module.codex_control_request(
                "realtime/calls",
                method="POST",
                payload=b"v=0\r\na=ice-pwd:failure-sdp-secret\r\n",
                query_params=[],
                headers={"content-type": "application/sdp"},
                access_token=access_token,
                account_id=account_id,
                session=cast(Any, Session()),
            )

    assert raised.value.status_code == 403
    assert "upstream_request_start" in caplog.text
    assert "upstream_request_complete" in caplog.text
    assert "error_code=upstream_error error_message=Upstream request failed" in caplog.text
    for secret in (
        account_id,
        access_token,
        raw_error_code,
        raw_error_message,
        "private-header-value",
        "failure-sdp-secret",
    ):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_live_connector_never_archives_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    sent_text: list[str] = []
    sent_bytes: list[bytes] = []

    class Connection:
        async def send(self, data: str | bytes) -> None:
            if isinstance(data, str):
                sent_text.append(data)
            else:
                sent_bytes.append(data)

        async def recv(self) -> str:
            return "response"

        async def close(self, code: int = 1000, reason: str = "") -> None:
            del code, reason

    async def fake_websocket_connect(*_args: Any, **_kwargs: Any) -> Connection:
        return Connection()

    def fail_archive(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("live sideband frames must not be archived")

    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )
    monkeypatch.setattr(proxy_websocket_module, "archive_text", fail_archive)
    monkeypatch.setattr(proxy_websocket_module, "archive_bytes", fail_archive)
    websocket = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        {},
        "account-token",
        "account-a",
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        allow_direct_egress=True,
    )

    await websocket.send_text("event")
    await websocket.send_bytes(b"audio")
    archive_received = getattr(websocket, "archive_received", None)
    assert callable(archive_received)
    archive_received(UpstreamWebSocketMessage(kind="text", text="response"))
    archive_received(UpstreamWebSocketMessage(kind="binary", data=b"response"))
    await websocket.close()

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
    wrapped = realtime_live_module._CloseOnceLiveWebSocket(cast(UpstreamWebSocket, upstream))
    await _relay_live_websocket(
        cast(Any, Downstream()),
        wrapped,
        max_message_bytes=1024,
        close_timeout_seconds=1,
    )

    assert upstream.text == ["event"]
    assert upstream.binary == [b"audio"]
    assert upstream.close_frames == [(1001, "client done")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("frame_key", "allowed_frame", "oversized_frame"),
    [
        ("text", "€", "€a"),
        ("bytes", b"abc", b"abcd"),
    ],
    ids=["text-utf8-bytes", "binary-bytes"],
)
async def test_live_relay_enforces_downstream_frame_byte_cap(
    frame_key: str,
    allowed_frame: str | bytes,
    oversized_frame: str | bytes,
) -> None:
    class Downstream:
        def __init__(self) -> None:
            self.application_state = WebSocketState.CONNECTED
            self.messages = [
                {"type": "websocket.receive", frame_key: allowed_frame},
                {"type": "websocket.receive", frame_key: oversized_frame},
            ]
            self.close_codes: list[int] = []

        async def receive(self) -> dict[str, Any]:
            return self.messages.pop(0)

        async def send_text(self, text: str) -> None:
            raise AssertionError(f"unexpected downstream text: {text}")

        async def send_bytes(self, data: bytes) -> None:
            raise AssertionError(f"unexpected downstream bytes: {data!r}")

        async def close(self, *, code: int, reason: str = "") -> None:
            del reason
            self.close_codes.append(code)
            self.application_state = WebSocketState.DISCONNECTED

    class Upstream(_FakeUpstreamWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.sent_text: list[str] = []
            self.sent_bytes: list[bytes] = []

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)

        async def send_bytes(self, data: bytes) -> None:
            self.sent_bytes.append(data)

    downstream = Downstream()
    upstream = Upstream()
    wrapped = realtime_live_module._CloseOnceLiveWebSocket(upstream)

    await _relay_live_websocket(
        cast(Any, downstream),
        wrapped,
        max_message_bytes=3,
        close_timeout_seconds=1,
    )

    assert upstream.sent_text == ([allowed_frame] if isinstance(allowed_frame, str) else [])
    assert upstream.sent_bytes == ([allowed_frame] if isinstance(allowed_frame, bytes) else [])
    assert upstream.close_calls == [(1009, "")]
    assert downstream.close_codes == [1009]


@pytest.mark.asyncio
async def test_live_relay_bounds_upstream_close_after_downstream_disconnect() -> None:
    class Downstream:
        async def receive(self) -> dict[str, Any]:
            return {"type": "websocket.disconnect", "code": 1001, "reason": "client done"}

        async def send_text(self, text: str) -> None:
            raise AssertionError(f"unexpected text: {text}")

        async def send_bytes(self, data: bytes) -> None:
            raise AssertionError(f"unexpected bytes: {data!r}")

        async def close(self, *, code: int, reason: str = "") -> None:
            del code, reason

    class HangingCloseUpstream(_FakeUpstreamWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.close_cancelled = asyncio.Event()

        async def close(self, code: int = 1000, reason: str = "") -> None:
            self.close_calls.append((code, reason))
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.close_cancelled.set()
                raise

    upstream = HangingCloseUpstream()
    wrapped = realtime_live_module._CloseOnceLiveWebSocket(upstream)

    with pytest.raises(TimeoutError):
        await _relay_live_websocket(
            cast(Any, Downstream()),
            wrapped,
            max_message_bytes=1024,
            close_timeout_seconds=0.01,
        )

    assert upstream.close_cancelled.is_set()
    assert upstream.close_calls == [(1001, "client done")]


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
    wrapped = realtime_live_module._CloseOnceLiveWebSocket(cast(UpstreamWebSocket, upstream))
    await _relay_live_websocket(
        cast(Any, downstream),
        wrapped,
        max_message_bytes=1024,
        close_timeout_seconds=1,
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

    wrapped = realtime_live_module._CloseOnceLiveWebSocket(cast(UpstreamWebSocket, Upstream()))

    task = asyncio.create_task(
        _relay_live_websocket(
            cast(Any, Downstream()),
            wrapped,
            max_message_bytes=1024,
            close_timeout_seconds=1,
        )
    )
    await asyncio.wait_for(asyncio.gather(downstream_started.wait(), upstream_started.wait()), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert downstream_stopped.is_set()
    assert upstream_stopped.is_set()


@pytest.mark.asyncio
async def test_live_sideband_cancellation_closes_both_peers_and_releases_lease() -> None:
    class HangingDownstream(_FakeDownstreamWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.accepted_event = asyncio.Event()

        async def accept(self, subprotocol: str | None = None) -> None:
            await super().accept(subprotocol=subprotocol)
            self.accepted_event.set()

        async def receive(self) -> dict[str, Any]:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    lease = cast(AccountLease, object())
    release_started = asyncio.Event()
    finish_release = asyncio.Event()

    class ContendedLoadBalancer(_FakeLoadBalancer):
        async def release_account_lease(self, lease) -> None:
            release_started.set()
            await finish_release.wait()
            self.released.append(lease)

    account = SimpleNamespace(
        id="account-a",
        status=AccountStatus.ACTIVE,
        access_token_encrypted="encrypted-token",
        chatgpt_account_id="chatgpt-account-a",
        codex_installation_id="installation-a",
    )
    downstream = HangingDownstream()
    upstream = _FakeUpstreamWebSocket()

    async def fake_connect_live_websocket(*_args, **_kwargs):
        return upstream

    service = _ProxyService(account, lease, live_websocket_connector=fake_connect_live_websocket)
    service._load_balancer = ContendedLoadBalancer()
    task = asyncio.create_task(
        service.proxy_realtime_live_websocket(
            cast(Any, downstream),
            "rtc_example",
            {},
            protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
            api_key=_unscoped_api_key(),
        )
    )
    await asyncio.wait_for(downstream.accepted_event.wait(), timeout=1)
    task.cancel()
    await asyncio.wait_for(release_started.wait(), timeout=1)
    task.cancel()
    finish_release.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert downstream.close_codes == [1011]
    assert upstream.close_calls == [(1000, "")]
    assert service._load_balancer.released == [lease]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [AccountStatus.ACTIVE, AccountStatus.REAUTH_REQUIRED],
    ids=["active", "reauth-required"],
)
@pytest.mark.parametrize(
    ("selected_subprotocol", "expected_accepted_subprotocol"),
    [(None, None), ("live.v1", "live.v1")],
    ids=["absent", "offered"],
)
async def test_live_sideband_accepts_only_absent_or_offered_upstream_subprotocol(
    status: AccountStatus,
    selected_subprotocol: str | None,
    expected_accepted_subprotocol: str | None,
) -> None:
    lease = cast(AccountLease, object())
    account = SimpleNamespace(
        id="account-a",
        status=status,
        access_token_encrypted="encrypted-token",
        chatgpt_account_id="chatgpt-account-a",
        codex_installation_id="installation-a",
    )
    downstream = _FakeDownstreamWebSocket(subprotocols=("live.v0", "live.v1"))
    upstream = _FakeUpstreamWebSocket(selected_subprotocol=selected_subprotocol)
    connector_subprotocols: tuple[str, ...] | None = None

    async def fake_connect_live_websocket(*_args, subprotocols, **_kwargs):
        nonlocal connector_subprotocols
        connector_subprotocols = subprotocols
        return upstream

    service = _ProxyService(account, lease, live_websocket_connector=fake_connect_live_websocket)

    await service.proxy_realtime_live_websocket(
        cast(Any, downstream),
        "rtc_example",
        {},
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        api_key=_unscoped_api_key(),
    )

    assert connector_subprotocols == ("live.v0", "live.v1")
    assert isinstance(connector_subprotocols, tuple)
    assert downstream.accepted_subprotocol == expected_accepted_subprotocol
    assert service._load_balancer.released == [lease]


@pytest.mark.asyncio
async def test_live_sideband_rejects_an_upstream_subprotocol_the_client_did_not_offer() -> None:
    lease = cast(AccountLease, object())
    account = SimpleNamespace(
        id="account-a",
        status=AccountStatus.ACTIVE,
        access_token_encrypted="encrypted-token",
        chatgpt_account_id="chatgpt-account-a",
        codex_installation_id="installation-a",
    )
    downstream = _FakeDownstreamWebSocket(subprotocols=("live.v0", "live.v1"))
    upstream = _FakeUpstreamWebSocket(selected_subprotocol="live.private")

    async def fake_connect_live_websocket(*_args, **_kwargs):
        return upstream

    service = _ProxyService(account, lease, live_websocket_connector=fake_connect_live_websocket)

    with pytest.raises(ProxyResponseError) as raised:
        await service.proxy_realtime_live_websocket(
            cast(Any, downstream),
            "rtc_example",
            {},
            protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
            api_key=_unscoped_api_key(),
        )

    assert raised.value.status_code == 502
    assert raised.value.payload["error"]["code"] == "upstream_websocket_subprotocol_mismatch"
    assert downstream.accepted is False
    assert upstream.close_calls == [(1000, "")]
    assert service._load_balancer.released == [lease]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
        AccountStatus.PAUSED,
        AccountStatus.DEACTIVATED,
    ],
    ids=["rate-limited", "quota-exceeded", "paused", "deactivated"],
)
async def test_live_sideband_fails_closed_when_fresh_owner_snapshot_is_unavailable(
    status: AccountStatus,
) -> None:
    lease = cast(AccountLease, object())
    selected_account = SimpleNamespace(id="account-a")
    unavailable_account = SimpleNamespace(
        id="account-a",
        status=status,
        access_token_encrypted="current-encrypted-token",
    )
    service = _ProxyService(selected_account, lease, current_account=unavailable_account)
    api_key = _unscoped_api_key()
    downstream = _FakeDownstreamWebSocket()

    with pytest.raises(ProxyResponseError) as raised:
        await service.proxy_realtime_live_websocket(
            cast(Any, downstream),
            "rtc_example",
            {},
            protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
            api_key=api_key,
        )

    assert raised.value.status_code == 503
    assert raised.value.payload["error"]["code"] == "continuity_owner_unavailable"
    assert service.accounts.fresh_reads == ["account-a"]
    assert service.decrypt_calls == []
    assert service._load_balancer.released == [lease]


@pytest.mark.asyncio
async def test_live_sideband_unavailable_exact_owner_never_falls_back_or_decrypts() -> None:
    service = _ProxyService(None, None)
    api_key = _unscoped_api_key()
    downstream = _FakeDownstreamWebSocket()

    with pytest.raises(ProxyResponseError) as raised:
        await service.proxy_realtime_live_websocket(
            cast(Any, downstream),
            "rtc_example",
            {},
            protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
            api_key=api_key,
        )

    assert raised.value.status_code == 503
    assert raised.value.payload["error"]["code"] == "continuity_owner_unavailable"
    assert downstream.accepted is False
    assert service.selection_calls[0]["redact_sensitive_details"] is True
    assert service.decrypt_calls == []
    assert service._load_balancer.released == [None]
