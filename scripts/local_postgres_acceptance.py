"""Exercise a prepared PostgreSQL pair through an isolated real TCP entrance."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import aiohttp

from scripts.local_stable_entry import Entry


async def verify(blue: int, green: int) -> None:
    # The validator inherits the operator-owned LOCAL_ENTRY_DATABASE_PLAN.
    if not os.environ.get("LOCAL_ENTRY_DATABASE_PLAN"):
        raise ValueError("PostgreSQL database plan required")
    with tempfile.TemporaryDirectory(prefix="codex-lb-pair-") as directory:
        state = Path(directory) / "state.json"
        state.write_text(json.dumps({"port": blue}))
        entry = Entry(state, [sys.executable, "-m", "scripts.local_entry_acceptance"])
        await entry.validate(blue)
        server = await asyncio.start_server(entry.relay, "127.0.0.1", 0)
        entry.listen_port = server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{entry.listen_port}"
        async with server, aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as client:
            async with client.ws_connect(base + "/backend-api/codex/responses") as socket:
                assert entry.counts[blue] == 1
                payload = {
                    "type": "response.create",
                    "model": "trae/GPT-5.6-Luna-max",
                    "stream": True,
                    "instructions": "Reply exactly PAIR_ACCEPTED. Do not call tools.",
                    "tools": [],
                    "input": [{"role": "user", "content": "Reply exactly PAIR_ACCEPTED"}],
                }
                await socket.send_json(payload)
                # Keep this established WebSocket on blue while new connections
                # move to green. Validate both completion and another turn on it.
                await entry.switch(green)
                assert entry.counts[blue] == 1
                for turn in range(2):
                    async with asyncio.timeout(50):
                        async for message in socket:
                            if message.type != aiohttp.WSMsgType.TEXT:
                                raise ValueError("Established WebSocket disconnected")
                            event = json.loads(message.data)
                            if event.get("type") in {"error", "response.failed"}:
                                raise ValueError("Established WebSocket inference failed")
                            if event.get("type") == "response.completed":
                                response = event["response"]
                                text = "".join(
                                    p.get("text", "") for i in response.get("output", []) for p in i.get("content", [])
                                )
                                if response.get("status") != "completed" or text.strip() != "PAIR_ACCEPTED":
                                    raise ValueError("Established WebSocket completion mismatch")
                                break
                        else:
                            raise ValueError("Established WebSocket ended")
                    if turn == 0:
                        await socket.send_json(payload)
                async with client.ws_connect(base + "/backend-api/codex/responses"):
                    assert entry.counts[green] == 1
                    await entry.switch(blue)
                    assert entry.counts[green] == 1 and entry.counts[blue] == 1
        async with asyncio.timeout(5):
            while any(entry.counts.values()):
                await asyncio.sleep(0.05)
        print("PostgreSQL pair: validated A→B→A; original WebSocket completed two turns; all connections drained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blue", type=int, default=2461)
    parser.add_argument("--green", type=int, default=2462)
    args = parser.parse_args()
    asyncio.run(verify(args.blue, args.green))
