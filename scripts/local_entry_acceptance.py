"""Acceptance probe for a local company-model backend: HTTP and WebSocket."""

from __future__ import annotations

import asyncio
import json
import os

import aiohttp


async def main() -> None:
    url = os.environ["CANDIDATE_URL"]
    payload = {
        "model": "trae/GPT-5.6-Luna-max",
        "stream": True,
        "instructions": "Reply exactly ENTRY_ACCEPTED. Do not call any tools.",
        "tools": [],
        "input": [
            {"type": "function_call_output", "name": "automation_update", "output": "probe context"},
            {"role": "user", "content": "Reply exactly ENTRY_ACCEPTED"},
        ],
    }

    def completed(event):
        if event.get("type") != "response.completed":
            return False
        response = event["response"]
        text = "".join(
            part.get("text", "")
            for item in response.get("output", [])
            for part in item.get("content", [])
            if part.get("type") == "output_text"
        )
        return response.get("status") == "completed" and text.strip() == "ENTRY_ACCEPTED"

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=50)) as client:
        async with client.post(url + "/backend-api/codex/responses", json=payload) as response:
            response.raise_for_status()
            found = False
            async for line in response.content:
                if line.startswith(b"data: ") and line.strip() != b"data: [DONE]":
                    found |= completed(json.loads(line[6:]))
            if not found:
                raise ValueError("HTTP acceptance failed")
        async with client.ws_connect(url + "/backend-api/codex/responses") as socket:
            await socket.send_json({"type": "response.create", **payload})
            async with asyncio.timeout(50):
                async for message in socket:
                    if message.type != aiohttp.WSMsgType.TEXT:
                        raise ValueError("WebSocket acceptance failed")
                    event = json.loads(message.data)
                    if completed(event):
                        return
                    if event.get("type") in {"error", "response.failed"}:
                        raise ValueError("WebSocket acceptance failed")
            raise ValueError("WebSocket ended without completion")


if __name__ == "__main__":
    asyncio.run(main())
