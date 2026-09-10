from __future__ import annotations

import asyncio
import contextlib
import os
import stat
from pathlib import Path

import anyio
import pytest

import app.core.clients.native_egress as native_egress_module
from app.core.clients.native_egress import (
    NativeEgressError,
    NativeEgressProtocolError,
    NativeEgressRequest,
    NativeEgressTransportError,
    NativeEgressUnavailable,
    NativeSseOptions,
    NativeWebSocketMessage,
    NativeWebSocketRequest,
    NativeWebSocketRoutingMetadata,
    SubprocessNativeEgressClient,
    close_discovered_native_egress_client,
    discover_native_egress_client,
)
from app.core.clients.stream_errors import StreamEventTooLargeError, StreamIdleTimeoutError

_HELPER_PROTOCOL_PREAMBLE = r"""
import json
import sys

hello = json.loads(sys.stdin.readline())
assert hello == {
    "type": "client_hello",
    "min_protocol_version": 1,
    "max_protocol_version": 1,
}
print(json.dumps({
    "type": "server_hello",
    "protocol_version": 1,
    "capabilities": [
        "failure_provenance_v1",
        "http",
        "http2_profile_v1",
        "http_compact_collect_v1",
        "http_compact_sse_v1",
        "http_sse_v1",
        "http_responses_events_v1",
        "http_responses_completion_v1",
        "websocket",
        "websocket_responses_events_v1",
        "websocket_responses_routing_v1",
        "websocket_send_ack",
    ],
}), flush=True)
"""


def _write_helper(path: Path, source: str) -> None:
    if source.startswith("#!/usr/bin/env python3\n"):
        source = source.replace(
            "#!/usr/bin/env python3\n",
            f"#!/usr/bin/env python3\n{_HELPER_PROTOCOL_PREAMBLE}\n",
            1,
        )
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _echo_helper_source() -> str:
    return """#!/usr/bin/env python3
import base64
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
        continue
    assert command["headers"] == [["accept", "text/event-stream"]]
    body = base64.b64decode(command["body"] or "")
    head = {
        "type": "head",
        "request_id": request_id,
        "status": 200,
        "http_version": "HTTP/2.0",
        "headers": [["content-type", "text/event-stream"]],
    }
    print(json.dumps(head), flush=True)
    payload = command["url"].rsplit("/", 1)[-1].encode() + b":" + body
    print(json.dumps({
        "type": "chunk",
        "request_id": request_id,
        "data": base64.b64encode(payload).decode(),
    }), flush=True)
    print(json.dumps({"type": "end", "request_id": request_id}), flush=True)
"""


@pytest.mark.asyncio
async def test_subprocess_native_egress_reuses_process_and_streams_response(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _echo_helper_source())
    client = SubprocessNativeEgressClient(helper)
    request = NativeEgressRequest(
        method="POST",
        url="https://example.test/codex/one",
        headers={"accept": "text/event-stream"},
        body=b"request-body",
    )

    first = await client.request(request)
    process = client._process
    assert first.status == 200
    assert first.http_version == "HTTP/2.0"
    assert first.raw_headers == (("content-type", "text/event-stream"),)
    assert first.headers["Content-Type"] == "text/event-stream"
    assert await first.read() == b"one:request-body"

    second = await client.request(
        NativeEgressRequest(
            method="POST",
            url="https://example.test/codex/two",
            headers={"accept": "text/event-stream"},
            body=b"next",
        )
    )
    assert await second.read() == b"two:next"
    assert client._process is process
    assert process is not None and process.returncode is None

    await client.aclose()
    assert process.returncode is not None


@pytest.mark.asyncio
async def test_subprocess_native_egress_rejects_incompatible_helper(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    helper.write_text(
        """#!/usr/bin/env python3
import json
import sys

json.loads(sys.stdin.readline())
print(json.dumps({
    "type": "server_hello",
    "protocol_version": 2,
    "capabilities": [],
}), flush=True)
sys.stdin.read()
""",
        encoding="utf-8",
    )
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    client = SubprocessNativeEgressClient(helper)

    with pytest.raises(NativeEgressProtocolError, match="unsupported protocol version"):
        await client.request(NativeEgressRequest(method="GET", url="https://example.test", headers={}))

    assert client._process is None


@pytest.mark.asyncio
async def test_subprocess_native_egress_demultiplexes_interleaved_requests(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys

requests = []
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": command["request_id"]}), flush=True)
        continue
    requests.append(command)
    if len(requests) != 2:
        continue
    for request in reversed(requests):
        print(json.dumps({
            "type": "head", "request_id": request["request_id"], "status": 200,
            "http_version": "HTTP/2.0", "headers": [],
        }), flush=True)
    for request in requests:
        payload = request["url"].rsplit("/", 1)[-1].encode()
        print(json.dumps({
            "type": "chunk", "request_id": request["request_id"],
            "data": base64.b64encode(payload).decode(),
        }), flush=True)
    for request in reversed(requests):
        print(json.dumps({"type": "end", "request_id": request["request_id"]}), flush=True)
    requests.clear()
""",
    )
    client = SubprocessNativeEgressClient(helper)

    left, right = await asyncio.gather(
        client.request(NativeEgressRequest(method="GET", url="https://example.test/left", headers={})),
        client.request(NativeEgressRequest(method="GET", url="https://example.test/right", headers={})),
    )
    left_body, right_body = await asyncio.gather(left.read(), right.read())

    assert left_body == b"left"
    assert right_body == b"right"
    await client.aclose()


@pytest.mark.asyncio
async def test_response_close_cancels_only_owned_request(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys

for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
        continue
    print(json.dumps({
        "type": "head", "request_id": request_id, "status": 200,
        "http_version": "HTTP/2.0", "headers": [],
    }), flush=True)
    if command["url"].endswith("/fast"):
        print(json.dumps({
            "type": "chunk", "request_id": request_id,
            "data": base64.b64encode(b"fast").decode(),
        }), flush=True)
        print(json.dumps({"type": "end", "request_id": request_id}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    slow = await client.request(NativeEgressRequest(method="GET", url="https://example.test/slow", headers={}))
    process = client._process

    await asyncio.wait_for(slow.aclose(), timeout=2.0)
    fast = await client.request(NativeEgressRequest(method="GET", url="https://example.test/fast", headers={}))

    assert await fast.read() == b"fast"
    assert client._process is process
    assert process is not None and process.returncode is None
    await client.aclose()


@pytest.mark.asyncio
async def test_helper_death_fails_generation_and_later_request_restarts(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    generation_file = tmp_path / "generation"
    _write_helper(
        helper,
        f"""#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import sys

generation_file = pathlib.Path({str(generation_file)!r})
generation = int(generation_file.read_text()) + 1 if generation_file.exists() else 1
generation_file.write_text(str(generation))
if generation == 1:
    requests = [json.loads(sys.stdin.readline()), json.loads(sys.stdin.readline())]
    os._exit(7)
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({{"type": "cancelled", "request_id": request_id}}), flush=True)
        continue
    print(json.dumps({{
        "type": "head", "request_id": request_id, "status": 200,
        "http_version": "HTTP/2.0", "headers": [],
    }}), flush=True)
    print(json.dumps({{
        "type": "chunk", "request_id": request_id,
        "data": base64.b64encode(b"restarted").decode(),
    }}), flush=True)
    print(json.dumps({{"type": "end", "request_id": request_id}}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)

    failures = await asyncio.gather(
        client.request(NativeEgressRequest(method="POST", url="https://example.test/one", headers={})),
        client.request(NativeEgressRequest(method="POST", url="https://example.test/two", headers={})),
        return_exceptions=True,
    )
    assert all(isinstance(result, NativeEgressError) for result in failures)
    old_generation = client._generation

    restarted = await client.request(
        NativeEgressRequest(method="GET", url="https://example.test/restarted", headers={})
    )

    assert await restarted.read() == b"restarted"
    assert client._generation == old_generation + 1
    assert generation_file.read_text() == "2"
    await client.aclose()


@pytest.mark.asyncio
async def test_subprocess_native_egress_rejects_missing_helper(tmp_path: Path) -> None:
    client = SubprocessNativeEgressClient(tmp_path / "missing")

    with pytest.raises(NativeEgressUnavailable):
        await client.request(NativeEgressRequest(method="GET", url="https://example.test", headers={}))


@pytest.mark.asyncio
async def test_subprocess_native_egress_rejects_invalid_first_event(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "request":
        print(json.dumps({"type": "chunk", "request_id": command["request_id"], "data": ""}), flush=True)
    else:
        print(json.dumps({"type": "cancelled", "request_id": command["request_id"]}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)

    with pytest.raises(NativeEgressProtocolError, match="head event"):
        await client.request(NativeEgressRequest(method="GET", url="https://example.test", headers={}))
    await client.aclose()


@pytest.mark.asyncio
async def test_subprocess_native_egress_buffers_json_error_body(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    head = {
        "type": "head", "request_id": request_id, "status": 429,
        "http_version": "HTTP/2.0", "headers": [],
    }
    print(json.dumps(head), flush=True)
    body = json.dumps({"error": {"code": "rate_limit_exceeded"}}).encode()
    print(json.dumps({"type": "chunk", "request_id": request_id, "data": base64.b64encode(body).decode()}), flush=True)
    print(json.dumps({"type": "end", "request_id": request_id}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    response = await client.request(NativeEgressRequest(method="GET", url="https://example.test", headers={}))

    assert await response.json() == {"error": {"code": "rate_limit_exceeded"}}
    assert await response.read() == b'{"error": {"code": "rate_limit_exceeded"}}'
    await client.aclose()


@pytest.mark.asyncio
async def test_subprocess_native_egress_preserves_helper_failure_provenance(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    print(json.dumps({
        "type": "error",
        "request_id": command["request_id"],
        "message": "native upstream connection failed",
        "failure_phase": "connect",
        "retryable_same_contract": True,
        "is_tls_verification_failure": True,
    }), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)

    with pytest.raises(NativeEgressTransportError) as exc_info:
        await client.request(NativeEgressRequest(method="GET", url="https://example.test", headers={}))

    assert exc_info.value.failure_phase == "connect"
    assert exc_info.value.retryable_same_contract is True
    assert exc_info.value.is_tls_verification_failure is True
    await client.aclose()


@pytest.mark.asyncio
async def test_client_close_is_idempotent_and_prevents_restart(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _echo_helper_source())
    client = SubprocessNativeEgressClient(helper)
    response = await client.request(
        NativeEgressRequest(
            method="GET",
            url="https://example.test/one",
            headers={"accept": "text/event-stream"},
        )
    )
    await response.read()
    process = client._process

    await client.aclose()
    await client.aclose()

    assert process is not None and process.returncode is not None
    with pytest.raises(NativeEgressUnavailable, match="closed"):
        await client.request(
            NativeEgressRequest(
                method="GET",
                url="https://example.test/two",
                headers={"accept": "text/event-stream"},
            )
        )


@pytest.mark.asyncio
async def test_buffered_body_burst_reaches_active_consumer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_egress_module, "_NATIVE_STREAM_QUEUE_LIMIT", 64)
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
        continue
    events = [{"type": "head", "request_id": request_id, "status": 200,
               "http_version": "HTTP/2.0", "headers": []}]
    for index in range(256):
        events.append({"type": "chunk", "request_id": request_id,
                       "data": base64.b64encode(str(index).encode() + b",").decode()})
    events.append({"type": "end", "request_id": request_id})
    sys.stdout.write("".join(json.dumps(event) + "\\n" for event in events))
    sys.stdout.flush()
""",
    )
    client = SubprocessNativeEgressClient(helper)
    try:
        response = await client.request(
            NativeEgressRequest(method="POST", url="https://example.test/responses", headers={}, body=b"{}")
        )
        assert await asyncio.wait_for(response.read(), timeout=2.0) == b"".join(
            str(index).encode() + b"," for index in range(256)
        )
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_client_close_does_not_hang_when_stream_queue_is_full(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
        continue
    print(json.dumps({
        "type": "head", "request_id": request_id, "status": 200,
        "http_version": "HTTP/2.0", "headers": [],
    }), flush=True)
    if command["url"].endswith("/slow-consumer"):
        # 48 x 1 MiB exceeds the 32 MiB per-request byte budget while staying
        # far below the 4096-event cap: the byte budget is what trips here.
        big = base64.b64encode(b"x" * (1024 * 1024)).decode()
        for _ in range(48):
            print(json.dumps({
                "type": "chunk", "request_id": request_id,
                "data": big,
            }), flush=True)
    else:
        print(json.dumps({
            "type": "chunk", "request_id": request_id,
            "data": base64.b64encode(b"ok").decode(),
        }), flush=True)
    print(json.dumps({"type": "end", "request_id": request_id}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    stalled = await client.request(
        NativeEgressRequest(method="GET", url="https://example.test/slow-consumer", headers={})
    )
    await asyncio.sleep(0.05)

    healthy = await client.request(NativeEgressRequest(method="GET", url="https://example.test/healthy", headers={}))

    assert await asyncio.wait_for(healthy.read(), timeout=2.0) == b"ok"
    with pytest.raises(NativeEgressTransportError, match="bounded event queue"):
        await stalled.read()

    await asyncio.wait_for(client.aclose(), timeout=2.0)


def test_native_helper_is_discovered_only_by_fixed_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = tmp_path / "codex-lb-native-egress"
    _write_helper(helper, "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    discover_native_egress_client.cache_clear()

    client = discover_native_egress_client()

    assert client is not None
    assert client.executable == helper
    discover_native_egress_client.cache_clear()


@pytest.mark.asyncio
async def test_close_discovered_helper_awaits_process_and_clears_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "codex-lb-native-egress"
    _write_helper(helper, _echo_helper_source())
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}")
    discover_native_egress_client.cache_clear()
    client = discover_native_egress_client()
    assert client is not None
    response = await client.request(
        NativeEgressRequest(
            method="GET",
            url="https://example.test/one",
            headers={"accept": "text/event-stream"},
        )
    )
    await response.read()
    process = client._process

    await close_discovered_native_egress_client()

    assert process is not None and process.returncode is not None
    replacement = discover_native_egress_client()
    assert replacement is not None and replacement is not client
    await close_discovered_native_egress_client()


def _websocket_helper_source() -> str:
    return """#!/usr/bin/env python3
import base64
import json
import sys

interpreted = set()
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    kind = command["type"]
    if kind == "websocket_connect":
        if command.get("interpret_responses"):
            interpreted.add(request_id)
        assert command["headers"] == [["user-agent", "codex-cli"], ["sec-websocket-protocol", "openai"]]
        assert command["ping_interval_ms"] == 20000
        assert command["ping_timeout_ms"] is None
        print(json.dumps({
            "type": "websocket_open", "request_id": request_id, "status": 101,
            "headers": [["sec-websocket-protocol", "openai"]],
        }), flush=True)
    elif kind == "websocket_send_text":
        print(json.dumps({
            "type": "websocket_responses_text" if request_id in interpreted else "websocket_text",
            "request_id": request_id,
            "text": command["text"] if request_id in interpreted else "echo:" + command["text"],
            **({"event_type": "response.text.delta",
                "payload": json.loads(command["text"]),
                "payload_response_id": "r1", "sequence_number": 17}
               if request_id in interpreted else {}),
        }), flush=True)
        print(json.dumps({
            "type": "websocket_sent", "request_id": request_id,
            "command_id": command["command_id"],
        }), flush=True)
    elif kind == "websocket_send_binary":
        print(json.dumps({
            "type": "websocket_binary", "request_id": request_id,
            "data": command["data"],
        }), flush=True)
        print(json.dumps({
            "type": "websocket_sent", "request_id": request_id,
            "command_id": command["command_id"],
        }), flush=True)
    elif kind == "websocket_close":
        print(json.dumps({
            "type": "websocket_sent", "request_id": request_id,
            "command_id": command["command_id"],
        }), flush=True)
        print(json.dumps({
            "type": "websocket_close", "request_id": request_id,
            "code": command["code"], "reason": command["reason"],
        }), flush=True)
    elif kind == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
"""


@pytest.mark.asyncio
async def test_native_websocket_routes_frames_and_send_acknowledgements(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _websocket_helper_source())
    client = SubprocessNativeEgressClient(helper)
    websocket = await client.websocket(
        NativeWebSocketRequest(
            url="wss://example.test/codex/responses",
            headers={"user-agent": "codex-cli", "sec-websocket-protocol": "openai"},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
        )
    )

    assert websocket.status == 101
    assert websocket.response_header("Sec-WebSocket-Protocol") == "openai"
    text_receive = asyncio.create_task(websocket.receive())
    await websocket.send_text("turn")
    assert await text_receive == NativeWebSocketMessage(kind="text", text="echo:turn")

    binary_receive = asyncio.create_task(websocket.receive())
    await websocket.send_bytes(b"\x00\xff")
    assert await binary_receive == NativeWebSocketMessage(kind="binary", data=b"\x00\xff")

    process = client._process
    await websocket.close(code=1000, reason="done")
    assert await websocket.receive() == NativeWebSocketMessage(kind="close", close_code=1000, close_reason="done")
    with pytest.raises(NativeEgressTransportError, match="closed"):
        await asyncio.wait_for(websocket.receive(), timeout=0.1)
    assert client._process is process
    assert process is not None and process.returncode is None
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "replacement",
    [
        None,
        ('"payload": json.loads(command["text"])', '"invalid_payload": None'),
        ('"payload_response_id": "r1"', '"missing_response_id": None'),
        ('"sequence_number": 17', '"missing_sequence_number": None'),
        ('"payload_response_id": "r1"', '"payload_response_id": 17'),
        ('"payload_response_id": "r1"', '"payload_response_id": True'),
        ('"sequence_number": 17', '"sequence_number": True'),
        ('"sequence_number": 17', '"sequence_number": 17.0'),
        ('"sequence_number": 17', '"sequence_number": "17"'),
        ('"sequence_number": 17', '"sequence_number": []'),
    ],
    ids=[
        "valid",
        "missing-payload",
        "missing-id",
        "missing-sequence",
        "int-id",
        "bool-id",
        "bool",
        "float",
        "str",
        "list",
    ],
)
async def test_native_responses_websocket_preserves_interpretation_metadata(
    tmp_path: Path, replacement: tuple[str, str] | None
) -> None:
    helper = tmp_path / "native-helper"
    source = _websocket_helper_source()
    if replacement is not None:
        source = source.replace(*replacement)
    _write_helper(helper, source)
    client = SubprocessNativeEgressClient(helper)
    websocket = await client.websocket(
        NativeWebSocketRequest(
            url="wss://example.test/codex/responses",
            headers={"user-agent": "codex-cli", "sec-websocket-protocol": "openai"},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
            interpret_responses=True,
        )
    )

    process = client._process
    if replacement is not None:
        with pytest.raises(NativeEgressProtocolError, match="Responses websocket event is invalid"):
            await websocket.send_text('{"type":"response.text.delta","delta":"hi"}')
            await websocket.receive()
        assert not client._streams
    else:
        await websocket.send_text('{"type":"response.text.delta","delta":"hi"}')
        assert await websocket.receive() == NativeWebSocketMessage(
            kind="text",
            text='{"type":"response.text.delta","delta":"hi"}',
            responses_interpreted=True,
            event_type="response.text.delta",
            payload={"type": "response.text.delta", "delta": "hi"},
            routing=NativeWebSocketRoutingMetadata("r1", 17),
        )
        await websocket.close()
    peer = await client.websocket(
        NativeWebSocketRequest(
            url="wss://example.test/codex/responses",
            headers={"user-agent": "codex-cli", "sec-websocket-protocol": "openai"},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
        )
    )
    await peer.send_text("healthy")
    assert await peer.receive() == NativeWebSocketMessage(kind="text", text="echo:healthy")
    await peer.close()
    assert client._process is process
    assert client._request_sequence == 2
    assert not client._streams
    await client.aclose()


@pytest.mark.asyncio
async def test_native_websocket_close_is_idempotent_after_peer_close_race(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "websocket_connect":
        print(json.dumps({
            "type": "websocket_open", "request_id": request_id,
            "status": 101, "headers": [],
        }), flush=True)
    elif command["type"] == "websocket_send_text":
        print(json.dumps({
            "type": "websocket_sent", "request_id": request_id,
            "command_id": command["command_id"],
        }), flush=True)
        print(json.dumps({
            "type": "websocket_close", "request_id": request_id,
            "code": 1000, "reason": "peer done",
        }), flush=True)
    elif command["type"] == "websocket_close":
        print(json.dumps({
            "type": "websocket_error", "request_id": request_id,
            "command_id": command["command_id"],
            "message": "native websocket is not active",
            "failure_phase": "setup", "retryable_same_contract": False,
            "is_tls_verification_failure": False,
            "status": None, "headers": [], "body": None,
        }), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    websocket = await client.websocket(
        NativeWebSocketRequest(
            url="wss://example.test/codex/responses",
            headers={},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
        )
    )

    await websocket.send_text("finish")
    peer_close = await websocket.receive()
    assert peer_close == NativeWebSocketMessage(
        kind="close",
        close_code=1000,
        close_reason="peer done",
    )
    await websocket.close()
    await websocket.close()
    await client.aclose()


@pytest.mark.asyncio
async def test_native_websocket_connections_are_isolated(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _websocket_helper_source())
    client = SubprocessNativeEgressClient(helper)
    request = NativeWebSocketRequest(
        url="wss://example.test/codex/responses",
        headers={"user-agent": "codex-cli", "sec-websocket-protocol": "openai"},
        connect_timeout_seconds=2,
        max_message_bytes=1024,
    )
    left, right = await asyncio.gather(client.websocket(request), client.websocket(request))

    left_receive = asyncio.create_task(left.receive())
    right_receive = asyncio.create_task(right.receive())
    await asyncio.gather(left.send_text("left"), right.send_text("right"))

    assert await left_receive == NativeWebSocketMessage(kind="text", text="echo:left")
    assert await right_receive == NativeWebSocketMessage(kind="text", text="echo:right")
    await asyncio.gather(left.close(), right.close())
    await client.aclose()


@pytest.mark.asyncio
async def test_native_websocket_preserves_handshake_denial(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys
command = json.loads(sys.stdin.readline())
print(json.dumps({
    "type": "websocket_error", "request_id": command["request_id"],
    "command_id": None, "message": "native websocket handshake failed",
    "failure_phase": "connect", "retryable_same_contract": False,
    "status": 429, "headers": [["content-type", "application/json"]],
    "body": base64.b64encode(b'{"error":{"code":"rate_limit_exceeded"}}').decode(),
}), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    print(json.dumps({"type": "cancelled", "request_id": command["request_id"]}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)

    with pytest.raises(NativeEgressTransportError) as exc_info:
        await client.websocket(
            NativeWebSocketRequest(
                url="wss://example.test/codex/responses",
                headers={},
                connect_timeout_seconds=2,
                max_message_bytes=1024,
            )
        )

    assert exc_info.value.status_code == 429
    assert exc_info.value.headers == (("content-type", "application/json"),)
    assert exc_info.value.body == b'{"error":{"code":"rate_limit_exceeded"}}'
    await client.aclose()


@pytest.mark.asyncio
async def test_native_websocket_preserves_liveness_timeout_phase(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import json
import sys
command = json.loads(sys.stdin.readline())
request_id = command["request_id"]
print(json.dumps({"type": "websocket_open", "request_id": request_id, "status": 101, "headers": []}), flush=True)
print(json.dumps({
    "type": "websocket_error", "request_id": request_id,
    "command_id": None, "message": "native websocket pong timed out",
    "failure_phase": "liveness_timeout", "retryable_same_contract": False,
    "status": None, "headers": [], "body": None,
}), flush=True)
for line in sys.stdin:
    command = json.loads(line)
    print(json.dumps({"type": "cancelled", "request_id": command["request_id"]}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    websocket = await client.websocket(
        NativeWebSocketRequest(
            url="wss://example.test/codex/responses",
            headers={},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
            ping_interval_seconds=0.02,
            ping_timeout_seconds=0.05,
        )
    )

    with pytest.raises(NativeEgressTransportError) as exc_info:
        await websocket.receive()

    assert exc_info.value.failure_phase == "liveness_timeout"
    await client.aclose()


@pytest.mark.asyncio
async def test_native_websocket_helper_death_fails_pending_send_without_replay(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import json
import os
import sys
command = json.loads(sys.stdin.readline())
print(json.dumps({
    "type": "websocket_open", "request_id": command["request_id"],
    "status": 101, "headers": [],
}), flush=True)
json.loads(sys.stdin.readline())
os._exit(9)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    websocket = await client.websocket(
        NativeWebSocketRequest(
            url="wss://example.test/codex/responses",
            headers={},
            connect_timeout_seconds=2,
            max_message_bytes=1024,
        )
    )

    with pytest.raises(NativeEgressError):
        await asyncio.wait_for(websocket.send_text("ambiguous"), timeout=2)

    assert client._generation == 1
    await client.aclose()


def _sse_helper_source(events: list[dict[str, object]]) -> str:
    return f"""#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({{"type": "cancelled", "request_id": request_id}}), flush=True)
        continue
    assert command["sse"] == {{"idle_timeout_ms": 1000, "max_event_bytes": 1024,
                              "content_type_aware": False, "collect_compact": False, "interpret_responses": False}}
    print(json.dumps({{"type": "head", "request_id": request_id, "status": 200,
                       "http_version": "HTTP/1.1", "headers": []}}), flush=True)
    for event in {events!r}:
        print(json.dumps({{**event, "request_id": request_id}}), flush=True)
"""


@pytest.mark.asyncio
async def test_native_sse_joins_fragments_and_yields_small_event_bursts(tmp_path: Path) -> None:
    blocks = [f"data: {i}\n\n" for i in range(200)]
    events: list[dict[str, object]] = [
        {"type": "sse", "text": "data: ", "more": True},
        {"type": "sse", "text": "한글\n\n", "more": False},
    ]
    for block in blocks:
        events.append({"type": "sse", "text": block, "more": False})
    events.append({"type": "end"})
    helper = tmp_path / "native-helper"
    _write_helper(helper, _sse_helper_source(events))
    client = SubprocessNativeEgressClient(helper)
    try:
        response = await client.request(
            NativeEgressRequest("GET", "https://example.test", {}, sse=NativeSseOptions(1, 1024))
        )
        async with response:
            assert [block async for block in response.iter_sse_events()] == ["data: 한글\n\n", *blocks]
        assert not client._streams
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("events", "error_type"),
    [
        ([{"type": "chunk", "data": "eA=="}], NativeEgressProtocolError),
        ([{"type": "sse", "text": 4, "more": False}], NativeEgressProtocolError),
        ([{"type": "sse", "text": "x", "more": "false"}], NativeEgressProtocolError),
        ([{"type": "sse", "text": "unfinished", "more": True}, {"type": "end"}], NativeEgressProtocolError),
        ([{"type": "sse_event_too_large", "size_bytes": True, "limit_bytes": 1}], NativeEgressProtocolError),
        ([{"type": "sse_event_too_large", "size_bytes": 1025, "limit_bytes": 1024}], StreamEventTooLargeError),
        ([{"type": "error", "failure_phase": "stream_idle_timeout"}], StreamIdleTimeoutError),
    ],
)
async def test_native_sse_failure_releases_owned_stream(
    tmp_path: Path, events: list[dict[str, object]], error_type: type[Exception]
) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _sse_helper_source(events))
    client = SubprocessNativeEgressClient(helper)
    try:
        response = await client.request(
            NativeEgressRequest("GET", "https://example.test", {}, sse=NativeSseOptions(1, 1024))
        )
        async with response, contextlib.aclosing(response.iter_sse_events()) as stream:
            with pytest.raises(error_type):
                await anext(stream)
        assert not client._streams
        assert client._process is not None and client._process.returncode is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "capability",
    [
        "http_sse_v1",
        "http_compact_sse_v1",
        "http_compact_collect_v1",
        "http_responses_events_v1",
        "http_responses_completion_v1",
        "websocket_responses_routing_v1",
    ],
)
async def test_native_sse_capability_is_required_before_dispatch(tmp_path: Path, capability: str) -> None:
    helper = tmp_path / "native-helper"
    preamble = _HELPER_PROTOCOL_PREAMBLE.replace(f'        "{capability}",\n', "")
    source = "#!/usr/bin/env python3\n" + preamble + "\nassert sys.stdin.readline() == ''\n"
    helper.write_text(source, encoding="utf-8")
    helper.chmod(helper.stat().st_mode | stat.S_IXUSR)
    client = SubprocessNativeEgressClient(helper)
    try:
        with pytest.raises(NativeEgressProtocolError, match=capability):
            await client.request(NativeEgressRequest("GET", "https://example.test", {}, sse=NativeSseOptions(1, 1024)))
        assert client._process is None
        assert not client._streams
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [{"type": "end"}],
        [{"type": "compact", "text": "{", "more": True}, {"type": "end"}],
        [{"type": "compact", "text": "not-json", "more": False}, {"type": "end"}],
        [{"type": "compact", "text": "{}", "more": "false"}],
        [{"type": "compact", "text": "x" * (16 * 1024 + 1), "more": False}],
        [{"type": "compact", "text": "{}", "more": False}] * 2 + [{"type": "end"}],
        [{"type": "sse", "text": "data: {}\n\n", "more": False}],
    ],
)
async def test_native_compact_rejects_broken_result_without_replay(
    tmp_path: Path,
    events: list[dict[str, object]],
) -> None:
    helper = tmp_path / "compact-helper"
    source = _sse_helper_source(events).replace(
        '"content_type_aware": False, "collect_compact": False',
        '"content_type_aware": True, "collect_compact": True',
    )
    _write_helper(helper, source)
    client = SubprocessNativeEgressClient(helper)
    try:
        response = await client.request(
            NativeEgressRequest(
                "POST",
                "https://example.test",
                {},
                sse=NativeSseOptions(1, 1024, True, True),
            )
        )
        with pytest.raises(NativeEgressProtocolError):
            await asyncio.wait_for(response.compact_result(), timeout=2)
        assert client._request_sequence == 1
        assert not client._streams
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        NativeSseOptions(0, 1),
        NativeSseOptions(float("nan"), 1),
        NativeSseOptions(float("inf"), 1),
        NativeSseOptions(1, 0),
        NativeSseOptions(1, True),
        NativeSseOptions(1, 1024, collect_compact=True),
        NativeSseOptions(1, 1024, content_type_aware=True, collect_compact=True, interpret_responses=True),
    ],
)
async def test_native_sse_options_are_validated_before_start(tmp_path: Path, options: NativeSseOptions) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _sse_helper_source([]))
    client = SubprocessNativeEgressClient(helper)
    try:
        with pytest.raises(ValueError):
            await client.request(NativeEgressRequest("GET", "https://example.test", {}, sse=options))
        assert client._process is None
        assert not client._streams
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_native_sse_close_finishes_in_cancelled_scope(tmp_path: Path) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(helper, _sse_helper_source([]))
    client = SubprocessNativeEgressClient(helper)
    try:
        response = await client.request(
            NativeEgressRequest("GET", "https://example.test", {}, sse=NativeSseOptions(1, 1024))
        )
        with anyio.CancelScope() as scope:
            scope.cancel()
            await response.aclose()
        assert not client._streams
        assert client._process is not None and client._process.returncode is None
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_native_request_cancelled_before_head_unregisters_stream_in_cancelled_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
for line in sys.stdin:
    command = json.loads(line)
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": command["request_id"]}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    sent = asyncio.Event()
    original_send = client._send_command

    async def send(process, generation, command):
        await original_send(process, generation, command)
        if command.get("type") == "request":
            sent.set()

    monkeypatch.setattr(client, "_send_command", send)
    scope_ready: asyncio.Future[anyio.CancelScope] = asyncio.get_running_loop().create_future()

    async def request() -> None:
        with anyio.CancelScope() as scope:
            scope_ready.set_result(scope)
            await client.request(NativeEgressRequest("POST", "https://example.test", {}, timeout_seconds=None))

    task = asyncio.create_task(request())
    try:
        scope = await scope_ready
        await asyncio.wait_for(sent.wait(), timeout=2)
        scope.cancel()
        await asyncio.wait_for(task, timeout=2)
        assert not client._streams
        assert client._process is not None and client._process.returncode is None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await client.aclose()


def test_bounded_event_queue_trips_on_bytes_or_events_and_releases_bytes_on_get() -> None:
    queue = native_egress_module._BoundedEventQueue(max_events=4, max_bytes=10)
    queue.put_nowait({"type": "chunk", "data": "abcd"})
    queue.put_nowait({"type": "sse", "text": "efgh"})
    assert queue.queued_bytes == 8 and not queue.full()
    # The incoming event counts: 8 queued + 3 would exceed the 10-byte budget.
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait({"type": "chunk", "data": "klm"})
    queue.put_nowait({"type": "chunk", "data": "ij"})  # exactly 10 bytes fills the budget
    assert queue.queued_bytes == 10
    # A zero-byte terminal at exactly the budget is still accepted (event cap has room)...
    queue.put_nowait({"type": "end"})
    assert queue.full()  # ...and 4 events is the event cap.
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait({"type": "cancelled"})
    assert queue.get_nowait() == {"type": "chunk", "data": "abcd"}
    assert queue.queued_bytes == 6 and not queue.full()
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait({"type": "chunk", "data": "wxyz!"})  # 6 + 5 > 10
    queue.put_nowait({"type": "chunk", "data": "wxyz"})  # 6 + 4 == 10
    while not queue.empty():
        queue.get_nowait()
    assert queue.queued_bytes == 0
    queue.put_nowait(RuntimeError("x"))
    assert queue.queued_bytes == 0
    queue.get_nowait()
    # Text payloads are measured in UTF-8 bytes, not code points.
    queue.put_nowait({"type": "sse", "text": "\u00e9\u00e9"})
    assert queue.queued_bytes == 4
    # Interpreted event metadata consumes the same byte budget as its text.
    interpreted = {"type": "responses_event", "text": "hi", "event_type": "\u00e9\u00e9"}
    queue.put_nowait(interpreted)
    assert queue.queued_bytes == 10
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait(interpreted)
    queue.get_nowait()
    assert queue.get_nowait() == interpreted
    assert queue.queued_bytes == 0
    # WebSocket IPC also embeds the original JSON object. Both copies count.
    websocket_event = {"type": "websocket_responses_text", "text": "{}", "payload": {}, "event_type": None}
    queue.put_nowait(websocket_event)
    queue.put_nowait(websocket_event)
    assert queue.queued_bytes == 8
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait(websocket_event)
    queue.get_nowait()
    queue.get_nowait()
    assert queue.queued_bytes == 0
    routed_event = {**websocket_event, "payload_response_id": "é", "sequence_number": -17}
    queue.put_nowait(routed_event)
    assert queue.queued_bytes == 9
    with pytest.raises(asyncio.QueueFull):
        queue.put_nowait(websocket_event)
    queue.get_nowait()
    assert queue.queued_bytes == 0
    # A lone event larger than the whole budget is accepted at an empty queue
    # (the SSE event size cap bounds it), so a single big chunk never fails;
    # anything but a zero-byte event is then rejected until it drains.
    big = native_egress_module._BoundedEventQueue(max_events=8, max_bytes=10)
    big.put_nowait({"type": "chunk", "data": "x" * 64})
    with pytest.raises(asyncio.QueueFull):
        big.put_nowait({"type": "chunk", "data": "y"})
    big.put_nowait({"type": "end"})


@pytest.mark.asyncio
async def test_burst_of_small_events_does_not_trip_the_queue_while_the_consumer_drains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hundreds of tiny framed deltas buffered in the helper pipe must not fail
    a healthy consumer (the pre-budget 64-event cap did exactly that on a
    saturated event loop, #2167)."""
    monkeypatch.setattr(native_egress_module, "_NATIVE_STREAM_QUEUE_LIMIT", 64)
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
        continue
    print(json.dumps({
        "type": "head", "request_id": request_id, "status": 200,
        "http_version": "HTTP/2.0", "headers": [],
    }), flush=True)
    out = []
    for _ in range(2000):
        out.append(json.dumps({
            "type": "chunk", "request_id": request_id,
            "data": base64.b64encode(b"delta").decode(),
        }))
    out.append(json.dumps({"type": "end", "request_id": request_id}))
    sys.stdout.write("\\n".join(out) + "\\n")
    sys.stdout.flush()
""",
    )
    client = SubprocessNativeEgressClient(helper)
    response = await client.request(NativeEgressRequest(method="GET", url="https://example.test/burst", headers={}))
    body = await asyncio.wait_for(response.read(), timeout=5.0)
    assert body == b"delta" * 2000
    await asyncio.wait_for(client.aclose(), timeout=2.0)


@pytest.mark.asyncio
async def test_response_landing_exactly_on_the_byte_budget_still_completes(tmp_path: Path) -> None:
    """16 x 2 MiB of base64 payload is exactly the 32 MiB budget; the zero-byte
    ``end`` that follows must still be accepted so the complete response is
    delivered once the consumer drains."""
    helper = tmp_path / "native-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import base64
import json
import sys
for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    if command["type"] == "cancel":
        print(json.dumps({"type": "cancelled", "request_id": request_id}), flush=True)
        continue
    print(json.dumps({
        "type": "head", "request_id": request_id, "status": 200,
        "http_version": "HTTP/2.0", "headers": [],
    }), flush=True)
    chunk = base64.b64encode(b"z" * (3 * 512 * 1024)).decode()  # 2 MiB of base64
    assert len(chunk) == 2 * 1024 * 1024
    for _ in range(16):
        print(json.dumps({"type": "chunk", "request_id": request_id, "data": chunk}), flush=True)
    print(json.dumps({"type": "end", "request_id": request_id}), flush=True)
""",
    )
    client = SubprocessNativeEgressClient(helper)
    response = await client.request(NativeEgressRequest(method="GET", url="https://example.test/exact", headers={}))
    await asyncio.sleep(1.0)  # let the whole body queue up before the consumer reads
    body = await asyncio.wait_for(response.read(), timeout=10.0)
    assert len(body) == 16 * 3 * 512 * 1024
    await asyncio.wait_for(client.aclose(), timeout=2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [{"type": "responses_event", "text": "data: {}\n\n", "more": False}],
        [{"type": "responses_event", "text": "x", "more": False, "event_type": 1, "python_normalization": False}],
        [
            {
                "type": "responses_event",
                "text": "x",
                "more": True,
                "event_type": "response.completed",
                "python_normalization": False,
            }
        ],
        [{"type": "responses_event", "text": "x", "more": False, "event_type": None, "python_normalization": "false"}],
        [
            {
                "type": "responses_event",
                "text": "x",
                "more": False,
                "event_type": "é" * (8 * 1024 + 1),
                "python_normalization": False,
            }
        ],
        [{"type": "responses_event", "text": "x", "more": True, "event_type": None, "python_normalization": True}],
        [
            {
                "type": "responses_event",
                "text": "x" * (16 * 1024 + 1),
                "more": False,
                "event_type": None,
                "python_normalization": False,
            }
        ],
        [
            {
                "type": "responses_event",
                "text": "unfinished",
                "more": True,
                "event_type": None,
                "python_normalization": False,
            },
            {"type": "end"},
        ],
        [{"type": "sse", "text": "data: {}\n\n", "more": False}],
        *[
            [
                {
                    "type": "responses_event",
                    "text": "data: {}\n\n",
                    "more": more,
                    "event_type": kind,
                    "python_normalization": False,
                    "stream_complete": complete,
                }
            ]
            for more, kind, complete in [
                (False, "response.completed", None),
                (False, "response.completed", 1),
                (False, "response.completed", "true"),
                (True, None, True),
                (False, "response.output_text.delta", True),
                (False, "error", True),
            ]
        ],
    ],
)
async def test_interpreted_sse_rejects_invalid_metadata_without_replay(
    tmp_path: Path, events: list[dict[str, object]]
) -> None:
    helper = tmp_path / "native-helper"
    for event in events:
        if event.get("type") == "responses_event":
            event.setdefault("stream_complete", False)
    source = _sse_helper_source(events).replace('"interpret_responses": False', '"interpret_responses": True')
    _write_helper(helper, source)
    client = SubprocessNativeEgressClient(helper)
    try:
        response = await client.request(
            NativeEgressRequest(
                "GET", "https://example.test", {}, sse=NativeSseOptions(1, 1024, interpret_responses=True)
            )
        )
        with pytest.raises(NativeEgressProtocolError):
            async for _ in response.iter_sse_events():
                pass
        assert client._request_sequence == 1
        assert not client._streams
    finally:
        await client.aclose()
