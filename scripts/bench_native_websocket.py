"""Compare native Responses WebSocket metadata against the old Python parse path."""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

import uvloop
from websockets.asyncio.server import serve

from app.core.clients.native_egress import NativeWebSocketRequest, SubprocessNativeEgressClient

EVENTS = 2048
SAMPLES = 12
WARMUPS = 3
PAYLOADS = [
    json.dumps({"type": "response.output_text.delta", "delta": "hello 한글"}, separators=(",", ":"))
    for _ in range(EVENTS)
]


async def main() -> None:
    clients = {
        mode: SubprocessNativeEgressClient(Path(os.environ["CODEX_LB_NATIVE_EGRESS_TEST_BINARY"]))
        for mode in ("raw", "interpreted")
    }

    async def handler(ws):
        if await ws.recv() != "go":
            return
        for payload in PAYLOADS:
            await ws.send(payload)

    results = {mode: [] for mode in clients}
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        for iteration in range(WARMUPS + SAMPLES):
            for mode, client in clients.items():
                start = time.perf_counter()
                before = _cpu(client)
                ws = await client.websocket(
                    NativeWebSocketRequest(
                        url=f"ws://127.0.0.1:{port}",
                        headers={},
                        connect_timeout_seconds=5,
                        max_message_bytes=1024 * 1024,
                        ping_interval_seconds=None,
                        interpret_responses=mode == "interpreted",
                    )
                )
                await ws.send_text("go")
                for _ in range(EVENTS):
                    message = await ws.receive()
                    assert message.kind == "text" and message.text is not None
                    if mode == "raw":
                        payload = json.loads(message.text)
                        assert payload["type"] == "response.output_text.delta"
                    else:
                        assert message.responses_interpreted and message.event_type == "response.output_text.delta"
                        assert message.payload is not None
                elapsed = (time.perf_counter() - start) * 1000
                cpu = (_cpu(client) - before) * 1000
                await ws.close()
                if iteration >= WARMUPS:
                    results[mode].append({"elapsed_ms": elapsed, "helper_cpu_ms": cpu})
    for client in clients.values():
        await client.aclose()
    summary = {}
    for mode, rows in results.items():
        summary[mode] = {
            k: {"median": statistics.median(r[k] for r in rows), "mean": statistics.mean(r[k] for r in rows)}
            for k in ("elapsed_ms", "helper_cpu_ms")
        }
    print(
        json.dumps(
            {
                "events": EVENTS,
                "samples": SAMPLES,
                "results": summary,
                "note": (
                    "Same helper binary, loopback websocket; raw parses each JSON frame in Python; "
                    "interpreted reuses the IPC payload and Rust classification."
                ),
            },
            indent=2,
        )
    )


def _cpu(client):
    process = client._process
    if process is None:
        return 0.0
    fields = Path(f"/proc/{process.pid}/stat").read_text().split(") ", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


uvloop.run(main())
