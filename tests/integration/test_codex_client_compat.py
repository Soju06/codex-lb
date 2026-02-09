from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _extract_first_event(lines: list[str]) -> dict:
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data event found")


@pytest.mark.asyncio
async def test_codex_style_responses_payload_is_accepted(async_client):
    payload = {
        "model": "gpt-5.1",
        "instructions": "You are Codex.",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "hi"},
                ],
            }
        ],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "stream": True,
        "include": ["message.output_text.logprobs"],
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] in ("response.failed", "response.completed", "response.incomplete")
