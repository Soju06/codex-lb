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

    # Public /v1/responses stream MUST start with `response.created` per the
    # OpenAI Responses SSE contract; the proxy synthesizes one when the
    # upstream stream skips it (e.g. when no accounts are available and
    # upstream emits only `response.failed`). The original intent of this
    # test is to verify the Codex-style payload (input_text, tool_choice,
    # parallel_tool_calls, include) is accepted and a stream begins —
    # whether the stream then completes, fails, or terminates depends on
    # upstream availability and is asserted elsewhere.
    event = _extract_first_event(lines)
    assert event["type"] == "response.created"
    # The terminal event (somewhere after response.created) should be one of
    # the known terminal types.
    terminal_types = {"response.completed", "response.incomplete",
                       "response.failed", "error"}
    terminal_events = [
        json.loads(line[6:])
        for line in lines
        if line.startswith("data: ") and not line.startswith("data: [DONE]")
        and json.loads(line[6:]).get("type") in terminal_types
    ]
    assert terminal_events, "Expected at least one terminal event in the stream"
