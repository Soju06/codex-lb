from __future__ import annotations

import asyncio
import json
import sys

import pytest
from aiohttp import web

from scripts.local_stable_entry import Entry


@pytest.mark.asyncio
async def test_real_connections_survive_switch_and_rollback(tmp_path):
    async def backend(reader, writer):
        while data := await reader.readline():
            writer.write(data)
            await writer.drain()
        writer.close()
        await writer.wait_closed()

    a = await asyncio.start_server(backend, "127.0.0.1", 0)
    b = await asyncio.start_server(backend, "127.0.0.1", 0)
    ports = [server.sockets[0].getsockname()[1] for server in (a, b)]
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"port": ports[0]}))
    entry = Entry(state, [sys.executable, "-c", "pass"])
    entrance = await asyncio.start_server(entry.relay, "127.0.0.1", 0)

    # Gate already has independent real HTTP/process tests below.
    async def validated(port):
        assert port in ports

    entry.validate = validated
    clients = []
    try:
        for target in (ports[0], ports[1], ports[0]):
            await entry.switch(target)
            reader, writer = await asyncio.open_connection("127.0.0.1", entrance.sockets[0].getsockname()[1])
            clients.append((reader, writer))
            writer.write(b"first\n")
            await writer.drain()
            assert await asyncio.wait_for(reader.readline(), 2) == b"first\n"
        assert entry.counts == {ports[0]: 2, ports[1]: 1}
        for reader, writer in clients:
            writer.write(b"still-streaming\n")
            await writer.drain()
            assert await asyncio.wait_for(reader.readline(), 2) == b"still-streaming\n"
    finally:
        for _, writer in clients:
            writer.close()
            await writer.wait_closed()
        for server in (entrance, a, b):
            server.close()
            await server.wait_closed()
        for _ in range(100):
            if not sum(entry.counts.values()):
                break
            await asyncio.sleep(0.01)
        assert sum(entry.counts.values()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("probe", ["raise SystemExit(1)", "import time; time.sleep(10)", "pass"])
async def test_actual_gate_failure_timeout_and_success(tmp_path, probe):
    app = web.Application()

    async def health(request):
        return web.json_response({"status": "ok"})

    async def models(request):
        return web.json_response({"models": [{"slug": "test"}]})

    app.router.add_get("/health", health)
    app.router.add_get("/backend-api/codex/models", models)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    state = tmp_path / "state.json"
    state.write_text('{"port": 12345}')
    entry = Entry(state, [sys.executable, "-c", probe], timeout=0.2)
    try:
        if probe == "pass":
            await entry.switch(port)
            assert entry.port == json.loads(state.read_text())["port"] == port
        else:
            with pytest.raises((ValueError, TimeoutError)):
                await entry.switch(port)
            assert entry.port == json.loads(state.read_text())["port"] == 12345
    finally:
        await runner.cleanup()
