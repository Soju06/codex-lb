from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from websockets.asyncio.server import serve

from app.core.clients.native_egress import NativeWebSocketRequest, SubprocessNativeEgressClient
from app.core.clients.proxy_websocket import NativeUpstreamWebSocket
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER
from app.core.utils.sse import parse_sse_data_json_text
from app.modules.proxy._service.http_bridge import upstream_events as bridge
from app.modules.proxy._service.websocket import mixin

FIXTURES = Path(__file__).resolve().parents[2] / "crates/codex-lb-responses/tests/fixtures/websocket-v1.json"


@pytest.mark.asyncio
async def test_native_websocket_values_and_policy_match_python(monkeypatch: pytest.MonkeyPatch) -> None:
    binary = os.environ.get("CODEX_LB_NATIVE_EGRESS_TEST_BINARY")
    if not binary:
        pytest.skip("set CODEX_LB_NATIVE_EGRESS_TEST_BINARY to run the native wire probe")
    cases = json.loads(FIXTURES.read_text())
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
                        with monkeypatch.context() as patch:
                            if interpreted:
                                patch.setattr(mixin, "json", SimpleNamespace(loads=reject_decode))
                            actual = mixin._parse_upstream_websocket_text_frame(text, message=message)
                        # NaN is intentionally opaque and is not equal to itself.
                        assert json.dumps(actual.payload) == json.dumps(expected.payload), case["name"]
                        assert actual.event_type == expected.event_type, case["name"]
                        assert actual.event == expected.event, case["name"]
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
                finally:
                    await websocket.close()
    finally:
        await client.aclose()
