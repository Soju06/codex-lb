"""Bounded inline-image bridge contract + WS close 1009 (allow-bounded-inline-images-on-bridge).

Real `/v1/responses` routes drive the existing HTTP responses bridge with NO
inline-image flag set (the production default is on): admissible inline
images ride the bridge session verbatim (connection reuse, ``prompt_cache_key``
continuity), a 5,000,001-byte decoded image and an over-budget complete frame
are rejected with the explicit 400 ``payload_too_large`` before any upstream
send (lane settled, same account serves the next request), the explicit-false
rollback restores the blanket bypass, and an upstream websocket close 1009 is
the terminal client error (400 before any event, SSE ``response.failed``
envelope after events) with no retry — while a generic disconnect keeps the
legacy ``stream_incomplete`` semantics. Upstream frames are synthetic:
compact single-line JSON only, no production image bytes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
import pytest_asyncio

import app.modules.proxy._service.http_bridge.helpers as http_bridge_helpers_module
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage as _FakeUpstreamMessage
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from app.modules.proxy._service import observability
from app.modules.proxy._service import support as proxy_support
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (  # noqa: F401 — autouse bridge-session cleanup
    _TEST_SYNC_TIMEOUT_SECONDS,
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_proxy_settings,
    _make_app_settings,
    _make_dashboard_settings,
    _PromotionUpstreamWebSocket,
    _SilentUpstreamWebSocket,
)

pytestmark = pytest.mark.integration

# Synthetic same-size payload: 132209 bytes of b"J", base64-encoded. NOT a
# real JPEG and no production image bytes; it proves size-class routing and
# verbatim transport only.
_JPEG_SIZED_SYNTHETIC_DATA_URL = "data:image/jpeg;base64," + base64.b64encode(b"J" * 132209).decode("ascii")
# A genuinely valid, deterministic 1x1 PNG so the positive route test proves a
# decodable image rides the bridge verbatim.
_VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_VALID_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(_VALID_PNG_BYTES).decode("ascii")
# Shape-only fixture (truncated PNG header): fine for bypass-shape tests.
_PNG_SHAPE_DATA_URL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="
# ~4.9 MB decoded: the operator-observed working shape (frame ~6.55 MB).
_UNDER_BUDGET_IMAGE_URL = "data:image/jpeg;base64," + base64.b64encode(b"J" * 4_900_000).decode("ascii")
# One decoded byte over the inclusive per-image budget.
_OVER_BUDGET_IMAGE_URL = "data:image/jpeg;base64," + base64.b64encode(b"J" * 5_000_001).decode("ascii")
_SMALL_LEGAL_IMAGE_URL = "data:image/png;base64," + base64.b64encode(b"P" * 2048).decode("ascii")
_THREAD_KEY = "inline-bounded-thread"


class _Close1009UpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    """Accepts the frame, then closes 1009 (message too big) before events.

    ``close_after_events`` streams ``response.created`` plus one delta first,
    proving the committed-stream branch (SSE error envelope, no replay).
    """

    def __init__(self, *, close_after_events: bool = False) -> None:
        super().__init__(response_id_prefix="resp_bridge_1009")
        self._close_after_events = close_after_events

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if self._close_after_events:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_1009_late", "object": "response", "status": "in_progress"},
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.output_text.delta",
                            "response_id": "resp_1009_late",
                            "delta": "partial",
                        },
                        separators=(",", ":"),
                    ),
                )
            )
        await self._messages.put(_FakeUpstreamMessage("close", close_code=1009, close_reason="message too big"))


class _GenericDisconnectUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    """Accepts the frame, then drops the socket without a close frame."""

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        await self._messages.put(_FakeUpstreamMessage("close", close_code=None))


class _ImageRejectUpstreamWebSocket(_FakeBridgeUpstreamWebSocket):
    """Rejects image-bearing frames before ``response.created``.

    Text-only frames get the healthy lifecycle, so one rejected image does
    not poison the socket. ``frame_style`` selects the real upstream failure
    frame; both are compact single-line JSON (the stock parser handles them
    without any multiline-JSON handling).
    """

    def __init__(self, *, frame_style: str = "error") -> None:
        super().__init__(response_id_prefix="resp_bridge")
        self._frame_style = frame_style

    async def send_text(self, text: str) -> None:
        if _collect_input_image_urls(text):
            self.sent_text.append(text)
            error = {
                "type": "invalid_request_error",
                "code": "invalid_image",
                "message": "Invalid image: the image payload could not be processed.",
            }
            if self._frame_style == "error":
                frame: dict[str, Any] = {"type": "error", "status": 400, "error": error}
            else:
                frame = {"type": "response.failed", "response": {"id": "resp_failed_image", "error": error}}
            await self._messages.put(_FakeUpstreamMessage("text", text=json.dumps(frame, separators=(",", ":"))))
            return
        await super().send_text(text)


def _text_turn(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def _image_turn(*image_urls: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": "describe the image"}]
    content.extend({"type": "input_image", "image_url": url} for url in image_urls)
    return {"role": "user", "content": content}


def _body(*input_items: dict[str, Any], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "gpt-5.4",
        "instructions": "Return exactly OK.",
        "input": list(input_items),
        "prompt_cache_key": _THREAD_KEY,
        "stream": True,
    }
    if tools is not None:
        body["tools"] = tools
    return body


def _collect_input_image_urls(frame_text: str) -> list[str]:
    urls: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "input_image" and isinstance(value.get("image_url"), str):
                urls.append(value["image_url"])
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(frame_text))
    return urls


def _raw_stream_recorder(monkeypatch: pytest.MonkeyPatch, raw_transports: list[str | None]) -> None:
    async def raw_stream(payload, *args, upstream_stream_transport_override=None, **kwargs):
        del payload, args, kwargs
        raw_transports.append(upstream_stream_transport_override)
        yield (
            "data: "
            + json.dumps({"type": "response.completed", "response": {"id": "resp_raw", "status": "completed"}})
            + "\n\n"
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", raw_stream)


@pytest_asyncio.fixture
async def inline_bridge_transport(async_client, app_instance, monkeypatch):
    """Real routes + bridge; NO inline-image flag set (production default).

    Only upstream I/O is faked. ``core_stream_responses`` starts as a hard
    guard: the default contract must serve image requests on the bridge,
    never the raw path (tests that deliberately exercise a fallback re-patch
    it with a recorder).
    """
    dashboard = _make_dashboard_settings()
    dashboard.http_downstream_transport_policy = "smart"
    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(enabled=True),
        dashboard_settings=dashboard,
    )
    account_id = await _import_account(async_client, "acc_inline_bounded", "inline-bounded@example.com")
    account = await _get_account(account_id)
    upstreams: list[Any] = [_PromotionUpstreamWebSocket(response_id_prefix="resp_inline_bounded")]
    state = SimpleNamespace(connects=0, upstreams=upstreams, account=account)

    async def fake_select_account_with_budget(self, *args, **kwargs):
        del self, args, kwargs
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, **kwargs):
        del self, kwargs
        return target

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        del headers, access_token, account_id_header, base_url, session
        upstream = upstreams[min(state.connects, len(upstreams) - 1)]
        state.connects += 1
        return upstream

    async def fail_legacy_stream(*args, **kwargs):
        raise AssertionError(
            "raw core_stream_responses path must not be used while the default contract serves the request"
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_legacy_stream)
    proxy_support.clear_upstream_websocket_transport_failure()
    yield state
    proxy_support.clear_upstream_websocket_transport_failure()
    await get_proxy_service_for_app(app_instance).drain_persistence_tasks(timeout_seconds=5.0)


@pytest.mark.asyncio
async def test_default_on_text_image_text_reuses_one_bridge_connection_verbatim(async_client, inline_bridge_transport):
    assert _VALID_PNG_BYTES[:8] == b"\x89PNG\r\n\x1a\n" and _VALID_PNG_BYTES[-8:-4] == b"IEND"
    state = inline_bridge_transport
    upstream = state.upstreams[0]

    first = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("hello")))
    second = await _collect_sse_events(
        async_client, "/v1/responses", json_body=_body(_text_turn("hello"), _image_turn(_VALID_PNG_DATA_URL))
    )
    third = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body=_body(
            _text_turn("hello"),
            _image_turn(_VALID_PNG_DATA_URL),
            _image_turn(_VALID_PNG_DATA_URL, _JPEG_SIZED_SYNTHETIC_DATA_URL),
            _text_turn("continue"),
        ),
    )

    _assert_created_text_delta_completed(first)
    _assert_created_text_delta_completed(second)
    _assert_created_text_delta_completed(third)
    # One upstream websocket connection carried all three turns — the image
    # turn opened no new bridge connection and no raw connection.
    assert state.connects == 1
    assert len(upstream.sent_text) == 3
    assert all(json.loads(frame).get("prompt_cache_key") == _THREAD_KEY for frame in upstream.sent_text)
    # The genuinely valid PNG bytes and the production-size synthetic segment
    # both arrive upstream verbatim, and only on the image-bearing turns.
    assert _collect_input_image_urls(upstream.sent_text[0]) == []
    assert _collect_input_image_urls(upstream.sent_text[1]) == [_VALID_PNG_DATA_URL]
    assert _collect_input_image_urls(upstream.sent_text[2]) == [
        _VALID_PNG_DATA_URL,
        _VALID_PNG_DATA_URL,
        _JPEG_SIZED_SYNTHETIC_DATA_URL,
    ]
    assert upstream.closed is False


@pytest.mark.asyncio
async def test_five_legal_images_and_long_history_fit_the_retained_budget(async_client, inline_bridge_transport):
    # Multiple individually legal images plus long history: the serialized
    # frame crosses the stock 16 MiB websocket budget but stays under the
    # 64 MiB image frame cap, so it is locally admitted without slimming.
    state = inline_bridge_transport
    history = "long context " * 250_000
    events = await _collect_sse_events(
        async_client,
        "/v1/responses",
        json_body=_body(_text_turn(history), _image_turn(*([_UNDER_BUDGET_IMAGE_URL] * 5))),
    )
    _assert_created_text_delta_completed(events)
    assert state.connects == 1
    frame = state.upstreams[0].sent_text[0]
    assert 16 * 1024 * 1024 < len(frame.encode("utf-8")) < 64 * 1024 * 1024
    assert _collect_input_image_urls(frame) == [_UNDER_BUDGET_IMAGE_URL] * 5
    sent = json.loads(frame)
    assert sent["input"][0]["content"] == history
    assert sent["prompt_cache_key"] == _THREAD_KEY


@pytest.mark.asyncio
async def test_over_budget_image_is_explicit_400_and_never_dispatched(async_client, inline_bridge_transport):
    state = inline_bridge_transport
    response = await async_client.post("/v1/responses", json=_body(_image_turn(_OVER_BUDGET_IMAGE_URL)))
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "input"
    assert "5000001" in error["message"]
    # Nothing was dispatched upstream and no bridge connection was opened.
    assert state.connects == 0
    assert state.upstreams[0].sent_text == []

    # Settlement: the very next request on the same thread and account
    # proceeds normally (no exclusion, no health penalty, no no_accounts
    # mask).
    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("after reject")))
    _assert_created_text_delta_completed(follow_up)
    assert state.connects == 1


@pytest.mark.asyncio
async def test_multiple_legal_images_cannot_exceed_the_frame_cap(async_client, inline_bridge_transport, monkeypatch):
    state = inline_bridge_transport
    monkeypatch.setattr(http_bridge_helpers_module, "_HTTP_BRIDGE_IMAGE_REQUEST_MAX_FRAME_BYTES", 512)
    # Two individually legal images; the serialized frame is far over the
    # shrunk cap — the explicit frame-budget 400, not a bypass.
    response = await async_client.post(
        "/v1/responses", json=_body(_image_turn(_SMALL_LEGAL_IMAGE_URL, _SMALL_LEGAL_IMAGE_URL))
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["param"] == "input"
    assert state.connects == 0


@pytest.mark.asyncio
async def test_explicit_false_rollback_restores_the_image_bypass(async_client, inline_bridge_transport, monkeypatch):
    state = inline_bridge_transport
    dashboard = _make_dashboard_settings()
    dashboard.http_downstream_transport_policy = "smart"
    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(enabled=True, inline_images_enabled=False),
        dashboard_settings=dashboard,
    )
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    raw_transports: list[str | None] = []
    _raw_stream_recorder(monkeypatch, raw_transports)
    response = await async_client.post("/v1/responses", json=_body(_image_turn(_UNDER_BUDGET_IMAGE_URL)))
    assert response.status_code == 200, response.text
    assert state.connects == 0
    # The raw path resolves the transport by the ordinary precedence ("auto"
    # under the smart dashboard policy), with no image-driven HTTP pin.
    assert raw_transports == ["auto"]
    routing_counter.labels.assert_any_call(stage="bypass", reason="image")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_items", "tools", "expected_raw_transport"),
    [
        pytest.param(
            [_image_turn(_PNG_SHAPE_DATA_URL, "https://example.com/shot.png")],
            None,
            "http",
            id="mixed_safe_and_external",
        ),
        pytest.param(
            [
                _text_turn("look"),
                {
                    "type": "function_call_output",
                    "call_id": "call_shot",
                    "output": [{"type": "input_image", "image_url": "https://example.com/shot.png"}],
                },
            ],
            None,
            "http",
            id="external_nested_in_tool_output",
        ),
        pytest.param([_image_turn("data:image/webp;base64,iVBORw0KGgo=")], None, "auto", id="unsupported_media_type"),
        pytest.param([_image_turn("data:image/png;base64,")], None, "auto", id="malformed_data_url"),
        pytest.param([_image_turn(_PNG_SHAPE_DATA_URL)], [{"type": "image_generation"}], "http", id="image_generation"),
    ],
)
async def test_unsupported_shapes_keep_the_bypass(
    async_client, inline_bridge_transport, monkeypatch, input_items, tools, expected_raw_transport
):
    state = inline_bridge_transport
    routing_counter = Mock()
    monkeypatch.setattr(observability, "http_bridge_routing_total", routing_counter)
    # Unsafe shapes fall back to the raw path by design: record it instead of
    # failing (overrides the fixture's raw-path guard).
    raw_transports: list[str | None] = []
    _raw_stream_recorder(monkeypatch, raw_transports)
    response = await async_client.post("/v1/responses", json=_body(*input_items, tools=tools))
    assert response.status_code == 200, response.text
    # The bridge was never connected; the blanket image bypass decided.
    assert state.connects == 0
    assert raw_transports == [expected_raw_transport]
    routing_counter.labels.assert_any_call(stage="bypass", reason="image")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "image_part",
    [
        pytest.param({"type": "input_image", "file_id": "file_img"}, id="file_id"),
        pytest.param({"type": "input_image", "image_url": "sediment://file_img"}, id="sediment_url"),
    ],
)
async def test_unsupported_uploaded_image_references_still_rejected_locally(
    async_client, inline_bridge_transport, image_part
):
    state = inline_bridge_transport
    body = _body({"role": "user", "content": [{"type": "input_text", "text": "see file"}, image_part]})
    response = await async_client.post("/v1/responses", json=body)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_input_image_format"
    assert state.connects == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frame_style",
    [pytest.param("error", id="error_frame"), pytest.param("response_failed", id="response_failed_frame")],
)
async def test_precreated_image_rejection_surfaces_and_releases_the_bridge(
    async_client, inline_bridge_transport, frame_style
):
    # An upstream that rejects the image BEFORE response.created — in either
    # real failure frame — surfaces the upstream error, settles the pending
    # slot, and the next good request on the same thread proceeds on the
    # SAME bridge connection.
    state = inline_bridge_transport
    rejecting = _ImageRejectUpstreamWebSocket(frame_style=frame_style)
    state.upstreams[0] = rejecting

    rejected = await async_client.post("/v1/responses", json=_body(_image_turn(_JPEG_SIZED_SYNTHETIC_DATA_URL)))
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "invalid_image"

    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("still there")))
    _assert_created_text_delta_completed(follow_up)
    # The rejecting upstream saw exactly the image frame, then the healthy
    # text follow-up on the SAME connection: the failed image released its
    # pending slot and opened no replacement connection.
    assert state.connects == 1
    assert len(rejecting.sent_text) == 2
    assert _collect_input_image_urls(rejecting.sent_text[0]) == [_JPEG_SIZED_SYNTHETIC_DATA_URL]
    assert _collect_input_image_urls(rejecting.sent_text[1]) == []


@pytest.mark.asyncio
async def test_close_1009_before_events_is_terminal_400_without_retry(async_client, inline_bridge_transport):
    state = inline_bridge_transport
    closing = _Close1009UpstreamWebSocket()
    state.upstreams[0] = closing

    response = await async_client.post("/v1/responses", json=_body(_image_turn(_SMALL_LEGAL_IMAGE_URL)))
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "payload_too_large"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "input"
    assert "1009" in error["message"]
    # Exactly one dispatch: no identical retry, no account rotation (the
    # replacement connection was never opened), no no_accounts mask.
    assert state.connects == 1
    assert len(closing.sent_text) == 1

    # Settlement + account availability: the follow-up text turn proceeds on
    # a fresh bridge connection with the SAME account.
    state.upstreams.append(_PromotionUpstreamWebSocket(response_id_prefix="resp_after_1009"))
    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("after 1009")))
    _assert_created_text_delta_completed(follow_up)
    assert state.connects == 2


@pytest.mark.asyncio
async def test_close_1009_after_events_terminates_sse_without_replay(async_client, inline_bridge_transport):
    state = inline_bridge_transport
    closing = _Close1009UpstreamWebSocket(close_after_events=True)
    state.upstreams[0] = closing

    events = await _collect_sse_events(
        async_client, "/v1/responses", json_body=_body(_image_turn(_SMALL_LEGAL_IMAGE_URL))
    )
    event_types = [event["type"] for event in events]
    assert event_types == ["response.created", "response.output_text.delta", "response.failed"]
    terminal = events[-1]["response"]["error"]
    assert terminal["code"] == "payload_too_large"
    assert terminal["type"] == "invalid_request_error"
    assert terminal["param"] == "input"
    # The streamed events appear exactly once — no duplicate output, no
    # replayed dispatch on a replacement socket.
    assert state.connects == 1
    assert len(closing.sent_text) == 1

    state.upstreams.append(_PromotionUpstreamWebSocket(response_id_prefix="resp_after_late_1009"))
    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("after late 1009")))
    _assert_created_text_delta_completed(follow_up)
    assert state.connects == 2


@pytest.mark.asyncio
async def test_generic_disconnect_is_not_reclassified(async_client, inline_bridge_transport):
    # Control for the 1009 classification: a socket dropped without a close
    # frame keeps the legacy ``stream_incomplete`` semantics — never the
    # payload_too_large client error.
    state = inline_bridge_transport
    dropping = _GenericDisconnectUpstreamWebSocket()
    state.upstreams[0] = dropping

    response = await async_client.post("/v1/responses", json=_body(_image_turn(_SMALL_LEGAL_IMAGE_URL)))
    if response.status_code == 200:
        terminal_events = [
            line for line in response.text.splitlines() if line.startswith("data: ") and "response.failed" in line
        ]
        assert terminal_events, response.text
        error = json.loads(terminal_events[-1][6:])["response"]["error"]
    else:
        assert response.status_code in (502, 503), response.text
        error = response.json()["error"]
    assert error["code"] == "stream_incomplete"
    assert state.connects >= 1


@pytest.mark.asyncio
async def test_silent_upstream_short_deadline_recovers_bounded(async_client, inline_bridge_transport, monkeypatch):
    # One genuinely silent connection, then a healthy one: the pre-created
    # eventless deadline (shrunk to tens of milliseconds) retires the silent
    # attempt and the single bounded retry recovers on a fresh connection.
    monkeypatch.setattr(http_bridge_helpers_module, "HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS", 0.01)
    state = inline_bridge_transport
    silent = _SilentUpstreamWebSocket()
    recovered = _PromotionUpstreamWebSocket(response_id_prefix="resp_inline_silent_recovered")
    state.upstreams[0] = silent
    state.upstreams.append(recovered)

    started_at = time.monotonic()
    events = await _collect_sse_events(
        async_client, "/v1/responses", json_body=_body(_image_turn(_JPEG_SIZED_SYNTHETIC_DATA_URL))
    )
    elapsed = time.monotonic() - started_at
    _assert_created_text_delta_completed(events)
    assert elapsed < _TEST_SYNC_TIMEOUT_SECONDS
    assert state.connects == 2
    assert silent.closed is True
    assert len(silent.sent_text) == 1
    assert _collect_input_image_urls(silent.sent_text[0]) == [_JPEG_SIZED_SYNTHETIC_DATA_URL]
    assert len(recovered.sent_text) == 1

    # The next good request on the same thread proceeds on the recovered
    # connection — no leaked pending slot, no third connection.
    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("after silence")))
    _assert_created_text_delta_completed(follow_up)
    assert state.connects == 2
    assert len(recovered.sent_text) == 2


@pytest.mark.asyncio
async def test_permanently_silent_image_terminates_bounded_then_recovers(app_instance, async_client, monkeypatch):
    # Every upstream connection stays silent: the request must TERMINATE via
    # the product's own pre-created deadline + single bounded retry, settle
    # its bridge state, and a later request must proceed once the upstream
    # turns healthy again.
    monkeypatch.setattr(http_bridge_helpers_module, "HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS", 0.01)
    dashboard = _make_dashboard_settings()
    dashboard.http_downstream_transport_policy = "smart"
    _install_proxy_settings(
        monkeypatch,
        app_settings=_make_app_settings(enabled=True),
        dashboard_settings=dashboard,
    )
    account_id = await _import_account(async_client, "acc_inline_perm_silent", "inline-perm-silent@example.com")
    account = await _get_account(account_id)

    healthy = _PromotionUpstreamWebSocket(response_id_prefix="resp_inline_perm_silent_recovered")
    silent_upstreams: list[_SilentUpstreamWebSocket] = []
    mode = {"silent": True}
    connects = 0

    async def fake_select_account_with_budget(self, *args, **kwargs):
        del self, args, kwargs
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, **kwargs):
        del self, kwargs
        return target

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connects
        connects += 1
        if mode["silent"]:
            silent_upstreams.append(_SilentUpstreamWebSocket())
            return silent_upstreams[-1]
        return healthy

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    proxy_support.clear_upstream_websocket_transport_failure()

    terminal = await async_client.post("/v1/responses", json=_body(_image_turn(_JPEG_SIZED_SYNTHETIC_DATA_URL)))
    # The product's own terminal path: the pre-created eventless deadline
    # fails the request after the initial send plus the single precreated
    # retry, surfaced as the typed upstream-unavailable envelope — never a
    # loop until the 7200 s request budget.
    assert terminal.status_code == 502, terminal.text
    terminal_body = terminal.json()
    assert terminal_body["error"]["code"] == "upstream_request_timeout"
    assert terminal_body["error"]["message"] == (
        "Upstream did not acknowledge response.create before the client-safe deadline"
    )
    # Bounded attempts: initial send plus the single precreated retry.
    assert connects == 2, f"expected bounded attempts, saw {connects} connections"
    assert all(len(upstream.sent_text) == 1 for upstream in silent_upstreams)

    service = get_proxy_service_for_app(app_instance)

    async def _bridge_settled() -> bool:
        async with service._http_bridge_lock:
            sessions = list(service._http_bridge_sessions.values())
        for session in sessions:
            async with session.pending_lock:
                if session.pending_requests or session.queued_request_count:
                    return False
        return True

    deadline = time.monotonic() + _TEST_SYNC_TIMEOUT_SECONDS
    while time.monotonic() < deadline and not await _bridge_settled():
        await asyncio.sleep(0.05)
    assert await _bridge_settled(), "terminally failed image request left bridge pending state unsettled"

    # Upstream turns healthy; the same thread proceeds on a fresh connection.
    mode["silent"] = False
    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("after silence")))
    _assert_created_text_delta_completed(follow_up)
    assert connects == 3
    assert len(healthy.sent_text) == 1


@pytest.mark.asyncio
async def test_client_cancellation_while_image_pending_settles(app_instance, async_client, monkeypatch):
    dashboard = _make_dashboard_settings()
    dashboard.http_downstream_transport_policy = "smart"
    app_settings = _make_app_settings(enabled=True)
    # Emit pre-created keepalives quickly so the test can cancel mid-wait.
    app_settings.sse_keepalive_interval_seconds = 0.05
    _install_proxy_settings(monkeypatch, app_settings=app_settings, dashboard_settings=dashboard)
    account_id = await _import_account(async_client, "acc_inline_cancel", "inline-cancel@example.com")
    account = await _get_account(account_id)
    silent = _SilentUpstreamWebSocket()
    recovered = _PromotionUpstreamWebSocket(response_id_prefix="resp_inline_cancel_recovered")

    async def fake_select_account_with_budget(self, *args, **kwargs):
        del self, args, kwargs
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fake_ensure_fresh_with_budget(self, target, **kwargs):
        del self, kwargs
        return target

    connects = 0

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        del headers, access_token, account_id_header, base_url, session
        nonlocal connects
        connects += 1
        return silent if connects == 1 else recovered

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", fake_select_account_with_budget)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh_with_budget)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    proxy_support.clear_upstream_websocket_transport_failure()

    service = get_proxy_service_for_app(app_instance)
    payload = proxy_module.ResponsesRequest(
        model="gpt-5.4",
        instructions="Return exactly OK.",
        input=[_image_turn(_JPEG_SIZED_SYNTHETIC_DATA_URL)],
        prompt_cache_key=_THREAD_KEY,
    )
    stream = service._stream_via_http_bridge(
        payload,
        {},
        codex_session_affinity=False,
        propagate_http_errors=False,
        openai_cache_affinity=True,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
        idle_ttl_seconds=120.0,
        codex_idle_ttl_seconds=900.0,
        max_sessions=128,
        queue_limit=8,
    )
    # First downstream frame before response.created is a keepalive; closing
    # the generator here models the client disconnecting while the image is
    # still pending upstream.
    first_frame = await asyncio.wait_for(stream.__anext__(), timeout=_TEST_SYNC_TIMEOUT_SECONDS)
    assert "keepalive" in first_frame
    await stream.aclose()

    session_key = proxy_module._HTTPBridgeSessionKey(
        affinity_kind="prompt_cache",
        affinity_key=_THREAD_KEY,
        api_key_id=None,
    )

    async def _bridge_settled() -> bool:
        async with service._http_bridge_lock:
            session = service._http_bridge_sessions.get(session_key)
        if session is None:
            return silent.closed
        async with session.pending_lock:
            settled = not session.pending_requests and session.queued_request_count == 0
        return settled and (session.closed or not session.pending_requests) and silent.closed

    deadline = time.monotonic() + _TEST_SYNC_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if await _bridge_settled():
            break
        await asyncio.sleep(0.05)
    assert await _bridge_settled(), "cancelled image request left bridge state unsettled"
    assert silent.closed is True

    # The subsequent good request proceeds on a fresh upstream.
    follow_up = await _collect_sse_events(async_client, "/v1/responses", json_body=_body(_text_turn("after cancel")))
    _assert_created_text_delta_completed(follow_up)
    assert connects == 2
    assert len(recovered.sent_text) == 1
