from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.clients.native_egress import (
    NativeEgressError,
    NativeEgressProtocolError,
    NativeEgressRequest,
    NativeEgressTransportError,
    NativeEgressUnavailable,
    NativeEgressWebSocket,
    NativeWebSocketMessage,
    NativeWebSocketRequest,
    SubprocessNativeEgressClient,
    close_discovered_native_egress_client,
    discover_native_egress_client,
)
from app.core.clients.websocket_dispatch import websocket_send_context

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
        "websocket",
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
        for _ in range(256):
            print(json.dumps({
                "type": "chunk", "request_id": request_id,
                "data": base64.b64encode(b"x").decode(),
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

for line in sys.stdin:
    command = json.loads(line)
    request_id = command["request_id"]
    kind = command["type"]
    if kind == "websocket_connect":
        assert command["headers"] == [["user-agent", "codex-cli"], ["sec-websocket-protocol", "openai"]]
        assert command["ping_interval_ms"] == 20000
        assert command["ping_timeout_ms"] is None
        print(json.dumps({
            "type": "websocket_open", "request_id": request_id, "status": 101,
            "headers": [["sec-websocket-protocol", "openai"]],
        }), flush=True)
    elif kind == "websocket_send_text":
        print(json.dumps({
            "type": "websocket_text", "request_id": request_id,
            "text": "echo:" + command["text"],
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


@dataclass(slots=True)
class _NativeDispatchHarness:
    websocket: NativeEgressWebSocket
    commands: asyncio.Queue[dict[str, object]]
    allow_ipc_return: asyncio.Event

    def acknowledge(self, command: dict[str, object]) -> None:
        self.websocket._events.put_nowait({"type": "websocket_sent", "command_id": command["command_id"]})

    def receive_marker(self, text: str) -> None:
        self.websocket._events.put_nowait({"type": "websocket_text", "text": text})


@pytest.fixture
async def native_dispatch_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_NativeDispatchHarness]:
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
    harness = _NativeDispatchHarness(websocket, asyncio.Queue(), asyncio.Event())
    original_send = client._send_command

    async def hold_command(process: asyncio.subprocess.Process, generation: int, command: dict[str, object]) -> None:
        if command["type"] == "websocket_close":
            await original_send(process, generation, command)
            return
        harness.commands.put_nowait(command)
        await harness.allow_ipc_return.wait()

    monkeypatch.setattr(client, "_send_command", hold_command)
    try:
        yield harness
    finally:
        harness.allow_ipc_return.set()
        await websocket.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_native_dispatch_ack_precedes_inbound_delivery_before_sender_resumes(
    native_dispatch_harness: _NativeDispatchHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = native_dispatch_harness
    dispatched: list[str] = []
    dispatch_at_delivery: list[tuple[str, ...]] = []
    original_queue_message = harness.websocket._queue_message

    def queue_message(message: NativeWebSocketMessage) -> None:
        dispatch_at_delivery.append(tuple(dispatched))
        original_queue_message(message)

    monkeypatch.setattr(harness.websocket, "_queue_message", queue_message)

    async def send() -> None:
        with websocket_send_context(lambda: dispatched.append("explicit")):
            await harness.websocket.send_text("explicit")

    sender = asyncio.create_task(send())
    try:
        command = await asyncio.wait_for(harness.commands.get(), timeout=2)
        assert dispatched == []  # The IPC write is not the socket handoff.
        harness.acknowledge(command)
        harness.acknowledge(command)
        harness.receive_marker("response.created")
        message = await asyncio.wait_for(harness.websocket.receive(), timeout=2)
        assert message.text == "response.created"
        assert dispatched == ["explicit"]
        assert dispatch_at_delivery == [("explicit",)]
        assert not sender.done()
        assert harness.websocket._pending == {}
        harness.allow_ipc_return.set()
        await asyncio.wait_for(sender, timeout=2)
        assert harness.websocket._pending == {}
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_dispatch_matches_exact_command_when_sends_overlap(
    native_dispatch_harness: _NativeDispatchHarness,
) -> None:
    harness = native_dispatch_harness
    dispatched: list[str] = []

    async def send(text: str) -> None:
        with websocket_send_context(lambda: dispatched.append(text)):
            await harness.websocket.send_text(text)

    left = asyncio.create_task(send("left"))
    right = asyncio.create_task(send("right"))
    try:
        commands = [await asyncio.wait_for(harness.commands.get(), timeout=2) for _ in range(2)]
        left_command = next(command for command in commands if command["text"] == "left")
        right_command = next(command for command in commands if command["text"] == "right")
        harness.acknowledge({"command_id": "unknown-command"})
        harness.acknowledge(right_command)
        harness.receive_marker("right")
        assert (await asyncio.wait_for(harness.websocket.receive(), timeout=2)).text == "right"
        assert dispatched == ["right"]
        harness.acknowledge(left_command)
        harness.receive_marker("left")
        assert (await asyncio.wait_for(harness.websocket.receive(), timeout=2)).text == "left"
        assert dispatched == ["right", "left"]
        harness.allow_ipc_return.set()
        await asyncio.wait_for(asyncio.gather(left, right), timeout=2)
    finally:
        left.cancel()
        right.cancel()
        await asyncio.gather(left, right, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_dispatch_does_not_observe_binary_or_close(
    native_dispatch_harness: _NativeDispatchHarness,
) -> None:
    harness = native_dispatch_harness
    dispatched: list[str] = []

    async def send() -> None:
        with websocket_send_context(lambda: dispatched.append("unexpected")):
            await harness.websocket.send_bytes(b"unrelated")
            await harness.websocket.close()

    sender = asyncio.create_task(send())
    try:
        command = await asyncio.wait_for(harness.commands.get(), timeout=2)
        harness.acknowledge(command)
        harness.allow_ipc_return.set()
        await asyncio.wait_for(sender, timeout=2)
        assert dispatched == []
        assert harness.websocket._pending == {}
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("ipc_returned", [False, True], ids=["ipc-wait", "ack-wait"])
async def test_native_dispatch_cancelled_send_ignores_late_ack(
    native_dispatch_harness: _NativeDispatchHarness, ipc_returned: bool
) -> None:
    harness = native_dispatch_harness
    dispatched: list[str] = []
    if ipc_returned:
        harness.allow_ipc_return.set()

    async def send(text: str) -> None:
        with websocket_send_context(lambda: dispatched.append(text)):
            await harness.websocket.send_text(text)

    cancelled = asyncio.create_task(send("cancelled"))
    replacement: asyncio.Task[None] | None = None
    try:
        old_command = await asyncio.wait_for(harness.commands.get(), timeout=2)
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert harness.websocket._pending == {}
        replacement = asyncio.create_task(send("replacement"))
        new_command = await asyncio.wait_for(harness.commands.get(), timeout=2)
        harness.acknowledge(old_command)
        harness.receive_marker("after-old-ack")
        assert (await asyncio.wait_for(harness.websocket.receive(), timeout=2)).text == "after-old-ack"
        assert dispatched == []
        harness.acknowledge(new_command)
        harness.allow_ipc_return.set()
        await asyncio.wait_for(replacement, timeout=2)
        assert dispatched == ["replacement"]
        assert harness.websocket._pending == {}
    finally:
        cancelled.cancel()
        if replacement is not None:
            replacement.cancel()
            await asyncio.gather(replacement, return_exceptions=True)
        await asyncio.gather(cancelled, return_exceptions=True)


@pytest.mark.asyncio
async def test_native_dispatch_send_failure_ignores_late_ack(
    native_dispatch_harness: _NativeDispatchHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = native_dispatch_harness
    dispatched: list[str] = []
    original_send = harness.websocket._client._send_command

    async def fail_command(process: asyncio.subprocess.Process, generation: int, command: dict[str, object]) -> None:
        if command["type"] == "websocket_close":
            await original_send(process, generation, command)
            return
        harness.commands.put_nowait(command)
        raise NativeEgressTransportError("IPC write failed", failure_phase="websocket_send")

    monkeypatch.setattr(harness.websocket._client, "_send_command", fail_command)
    with websocket_send_context(lambda: dispatched.append("failed")):
        with pytest.raises(NativeEgressTransportError, match="IPC write failed"):
            await harness.websocket.send_text("failed")
    command = await asyncio.wait_for(harness.commands.get(), timeout=2)
    assert harness.websocket._pending == {}
    harness.acknowledge(command)
    harness.receive_marker("after-failure")
    assert (await asyncio.wait_for(harness.websocket.receive(), timeout=2)).text == "after-failure"
    assert dispatched == []


@pytest.mark.asyncio
async def test_native_dispatch_worker_error_fails_pending_send_without_notification(
    native_dispatch_harness: _NativeDispatchHarness,
) -> None:
    harness = native_dispatch_harness
    dispatched: list[str] = []

    async def send() -> None:
        with websocket_send_context(lambda: dispatched.append("failed")):
            await harness.websocket.send_text("failed")

    sender = asyncio.create_task(send())
    try:
        command = await asyncio.wait_for(harness.commands.get(), timeout=2)
        harness.websocket._events.put_nowait(
            {
                "type": "websocket_error",
                "command_id": command["command_id"],
                "message": "socket write failed",
                "failure_phase": "websocket_send",
            }
        )
        harness.acknowledge(command)
        with pytest.raises(NativeEgressTransportError, match="socket write failed"):
            await asyncio.wait_for(harness.websocket.receive(), timeout=2)
        assert harness.websocket._pending == {}
        assert not sender.done()
        harness.allow_ipc_return.set()
        with pytest.raises(NativeEgressTransportError, match="socket write failed"):
            await asyncio.wait_for(sender, timeout=2)
        assert dispatched == []
        assert harness.websocket._pending == {}
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)


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
