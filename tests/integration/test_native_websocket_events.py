from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from websockets.asyncio.server import serve

from app.core.clients.native_egress import NativeWebSocketRequest, SubprocessNativeEgressClient
from app.core.clients.proxy_websocket import NativeUpstreamWebSocket
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER
from app.core.openai.parsing import _LIFECYCLE_EVENT_TYPES, classify_event_type, parse_sse_event_payload
from app.core.utils.sse import parse_sse_data_json_text
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import upstream_events as bridge
from app.modules.proxy._service.websocket import mixin

FIXTURES = Path(__file__).resolve().parents[2] / "crates/codex-lb-responses/tests/fixtures/websocket-v1.json"


@pytest.mark.asyncio
async def test_native_websocket_oversized_integers_preserve_peer_exchanges() -> None:
    binary = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not binary:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native wire probe")

    async def echo(websocket: Any) -> None:
        async for text in websocket:
            await websocket.send(text)

    client = SubprocessNativeEgressClient(Path(binary))
    original_limit = sys.get_int_max_str_digits()
    sockets: list[NativeUpstreamWebSocket] = []
    try:
        # The helper must also be safe with Python's smallest enabled limit.
        sys.set_int_max_str_digits(sys.int_info.str_digits_check_threshold)
        async with serve(echo, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            for _ in range(2):
                sockets.append(
                    NativeUpstreamWebSocket(
                        await client.websocket(
                            NativeWebSocketRequest(
                                url=f"ws://127.0.0.1:{port}/responses",
                                headers={},
                                connect_timeout_seconds=5,
                                max_message_bytes=1024 * 1024,
                                interpret_responses=True,
                            )
                        )
                    )
                )
            process = client._process
            assert process is not None
            for digits in (641, 5000):
                token = "-" + "9" * digits
                for fields in (
                    f'"sequence_number":{token}',
                    f'"response":{{"extra":[{token}]}},"sequence_number":1',
                    f'"sequence_number":{token},"sequence_number":1',
                ):
                    text = '{"type":"response.output_text.delta",' + fields + "}"
                    await sockets[0].send_text(text)
                    message = await sockets[0].receive()
                    assert message.kind == "text"
                    assert message.text == text
                    assert not message.responses_interpreted
                    # The legacy parser may reject the affected frame, but it
                    # must not take down the shared IPC reader or its peers.
                    with pytest.raises(ValueError, match="integer string conversion"):
                        mixin._parse_upstream_websocket_text_frame(text, message=message)
                    for websocket in sockets:
                        healthy = '{"type":"response.output_text.delta","sequence_number":' + "9" * 640 + "}"
                        await websocket.send_text(healthy)
                        reply = await websocket.receive()
                        assert reply.kind == "text"
                        assert reply.text == healthy
                        assert reply.responses_interpreted
                        assert reply.routing is not None
                        assert reply.routing.sequence_number == int("9" * 640)
                    assert client._process is process
                    assert process.returncode is None
            for websocket in sockets:
                await websocket.close()
    finally:
        await client.aclose()
        sys.set_int_max_str_digits(original_limit)


@pytest.mark.asyncio
async def test_native_websocket_values_and_policy_match_python(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not binary:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native wire probe")
    cases = json.loads(FIXTURES.read_text())
    cases += json.loads(FIXTURES.with_name("websocket-routing-v1.json").read_text())
    prefix = '{"type":"response.output_text.delta","delta":"'
    cases.append(
        {
            "name": "interpretation_size_boundary",
            "text": prefix + "x" * (1024 * 1024 - len(prefix) - 2) + '"}',
            "interpreted": True,
            "event_type": "response.output_text.delta",
        }
    )
    cases.append(
        {
            "name": "large_opaque_object",
            "text": json.dumps({"type": "response.output_text.delta", "delta": "x" * (1024 * 1024)}),
            "interpreted": False,
            "event_type": "response.output_text.delta",
        }
    )

    async def echo(websocket: Any) -> None:
        async for text in websocket:
            await websocket.send(text)

    def reject_decode(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("native policy must reuse the IPC payload")

    client = SubprocessNativeEgressClient(Path(binary))
    try:
        async with serve(echo, "127.0.0.1", 0, max_size=2 * 1024 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            # False covers the opaque contract also used by Live WebSockets.
            for interpret in (False, True):
                websocket = NativeUpstreamWebSocket(
                    await client.websocket(
                        NativeWebSocketRequest(
                            url=f"ws://127.0.0.1:{port}/responses",
                            headers={},
                            connect_timeout_seconds=5,
                            max_message_bytes=2 * 1024 * 1024,
                            interpret_responses=interpret,
                        )
                    )
                )
                try:
                    for case in cases:
                        text = case["text"]
                        expected = mixin._parse_upstream_websocket_text_frame(text)
                        await websocket.send_text(text)
                        message = await websocket.receive()
                        assert message.kind == "text", case["name"]
                        assert message.text == text, case["name"]
                        interpreted = interpret and case["interpreted"]
                        assert message.responses_interpreted == interpreted, case["name"]
                        if interpreted:
                            assert message.payload == expected.payload, case["name"]
                            assert message.event_type == case["event_type"], case["name"]
                            assert message.routing is not None, case["name"]
                            assert message.routing.payload_response_id == mixin._websocket_response_id(
                                None, expected.payload
                            ), case["name"]
                            assert message.routing.sequence_number == expected.sequence_number, case["name"]
                        with monkeypatch.context() as patch:
                            if interpreted:
                                patch.setattr(mixin, "json", SimpleNamespace(loads=reject_decode))
                            actual = mixin._parse_upstream_websocket_text_frame(text, message=message)
                        # NaN is intentionally opaque and is not equal to itself.
                        assert json.dumps(actual.payload) == json.dumps(expected.payload), case["name"]
                        assert actual.event_type == expected.event_type, case["name"]
                        assert actual.event == expected.event, case["name"]
                        assert actual.response_id == expected.response_id, case["name"]
                        assert actual.sequence_number == expected.sequence_number, case["name"]
                        process = AsyncMock()
                        harness = SimpleNamespace(_process_parsed_http_bridge_upstream_event=process)
                        expected_bridge_payload = parse_sse_data_json_text(text)
                        with monkeypatch.context() as patch:
                            if interpreted and text.startswith("{") and "\n" not in text and "\r" not in text:
                                patch.setattr(bridge, "parse_sse_data_json_text", reject_decode)
                            await bridge._HTTPBridgeUpstreamEventsMixin._process_http_bridge_upstream_text(
                                harness,
                                cast(Any, None),
                                text,
                                message=message,
                                scheduler=REAL_SCHEDULER,
                                clock=REAL_CLOCK,
                            )
                        assert process.await_args is not None
                        processed = process.await_args.kwargs
                        assert json.dumps(processed["payload"]) == json.dumps(expected_bridge_payload), case["name"]
                        assert processed["text"] == text
                        assert processed["event_block"] == f"data: {text}\n\n"
                        bridge_type = classify_event_type(expected_bridge_payload)
                        bridge_event = (
                            parse_sse_event_payload(expected_bridge_payload)
                            if bridge_type in _LIFECYCLE_EVENT_TYPES
                            else None
                        )
                        assert processed["response_id"] == mixin._websocket_response_id(
                            bridge_event, expected_bridge_payload
                        ), case["name"]
                finally:
                    await websocket.close()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("interpret", [False, True], ids=["opaque", "native"])
@pytest.mark.parametrize("send_failure", [False, True], ids=["delivered", "send-failed"])
async def test_native_websocket_routing_preserves_delivery_watermarks(
    monkeypatch: pytest.MonkeyPatch, interpret: bool, send_failure: bool
) -> None:
    binary = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not binary:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native wire probe")

    async def echo(websocket: Any) -> None:
        async for text in websocket:
            await websocket.send(text)

    service = proxy_service.ProxyService(cast(Any, None))
    monkeypatch.setattr(service, "_touch_active_websocket_thread_affinity", AsyncMock())
    failed = AsyncMock()
    monkeypatch.setattr(service, "_fail_pending_websocket_requests", failed)
    account = cast(Any, SimpleNamespace(id="account"))
    states = [
        proxy_service._WebSocketRequestState(
            request_id=f"request-{i}",
            response_id=f"r{i}",
            archive_request_id=f"archive-{i}",
            model="gpt-5.6-sol",
            service_tier=None,
            reasoning_effort=None,
            api_key_reservation=None,
            started_at=REAL_CLOCK.monotonic(),
        )
        for i in (1, 2)
    ]
    states[1].replay_downstream_response_id = "visible-r2"
    states[1].last_downstream_sequence_number = 5
    pending = deque(states)
    pending_lock = anyio.Lock()
    control = proxy_service._WebSocketUpstreamControl()
    sent: list[str] = []

    async def send_text(text: str) -> None:
        if send_failure and json.loads(text).get("response_id") == "visible-r2":
            raise ConnectionError("downstream disconnected")
        sent.append(text)

    downstream = cast(Any, SimpleNamespace(send_text=send_text))
    client = SubprocessNativeEgressClient(Path(binary))
    try:
        async with serve(echo, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            upstream = NativeUpstreamWebSocket(
                await client.websocket(
                    NativeWebSocketRequest(
                        url=f"ws://127.0.0.1:{port}/responses",
                        headers={},
                        connect_timeout_seconds=5,
                        max_message_bytes=1024 * 1024,
                        interpret_responses=interpret,
                    )
                )
            )
            try:

                async def relay(payload: dict[str, Any], archive_id: str) -> bool:
                    text = json.dumps(payload, separators=(",", ":"))
                    await upstream.send_text(text)
                    message = await upstream.receive()
                    assert message.kind == "text"
                    assert (
                        await mixin._websocket_archive_request_id_for_message(
                            message, pending_requests=pending, pending_lock=pending_lock
                        )
                        == archive_id
                    )
                    return await mixin._process_and_forward_upstream_websocket_text(
                        service,
                        downstream,
                        upstream,
                        message=message,
                        text=text,
                        account=account,
                        account_id_value=account.id,
                        pending_requests=pending,
                        pending_lock=pending_lock,
                        client_send_lock=anyio.Lock(),
                        api_key=None,
                        upstream_control=control,
                        response_create_gate=asyncio.Semaphore(1),
                        downstream_activity=proxy_service._DownstreamWebSocketActivity(),
                        continuity_state=None,
                        codex_session_affinity=False,
                        clock=REAL_CLOCK,
                    )

                assert not await relay(
                    {
                        "type": "response.output_text.delta",
                        "response_id": "r1",
                        "sequence_number": True,
                        "delta": "one",
                    },
                    "archive-1",
                )
                assert states[0].last_downstream_sequence_number is None
                large_sequence = 10**100 + 7
                delta = {
                    "type": "response.output_text.delta",
                    "response_id": " r2 ",
                    "sequence_number": large_sequence,
                    "delta": "two",
                }
                assert await relay(delta, "archive-2") is send_failure
                assert states[1].last_downstream_sequence_number == (5 if send_failure else large_sequence)
                assert states[0].last_downstream_sequence_number is None
                if send_failure:
                    assert len(sent) == 1
                    failed.assert_awaited_once()
                else:
                    assert json.loads(sent[-1])["response_id"] == "visible-r2"
                    states[1].suppress_next_created_downstream = True
                    assert not await relay(
                        {"type": "response.created", "response": {"id": "r2"}, "sequence_number": 0},
                        "archive-2",
                    )
                    assert len(sent) == 2
                    assert states[1].last_downstream_sequence_number == large_sequence
                    with pytest.raises(mixin._WebSocketReplaySequenceRegression):
                        await relay(delta, "archive-2")
                    assert len(sent) == 2
                    failed.assert_not_awaited()
            finally:
                await upstream.close()
    finally:
        await client.aclose()
