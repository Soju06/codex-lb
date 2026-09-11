"""Explicit opt-in live probe; uses only the suite's isolated DB and synthetic inputs."""

from __future__ import annotations

import json
import os

import pytest

from app.modules.model_sources.trae import TRAE_BASE_URL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CODEX_LB_TEST_TRAE_LIVE") != "1", reason="Explicit live LLMBox opt-in required"),
]


@pytest.mark.asyncio
async def test_live_trae_responses_tool_roundtrip(async_client):
    created = await async_client.post(
        "/api/model-sources/",
        json={
            "kind": "trae",
            "name": "Isolated live probe",
            "baseUrl": TRAE_BASE_URL,
            "supportsResponses": True,
            "supportsChatCompletions": False,
            "timeoutSeconds": 900,
            "models": [
                {
                    "model": "trae/GPT-6-Astra",
                    "supportsTools": True,
                    "supportsStreaming": True,
                    "rawMetadataJson": json.dumps(
                        {
                            "supports_reasoning": True,
                            "trae_config_name": "gpt-6-astra",
                            "trae_model_name": "gpt-6-astra__dev",
                        }
                    ),
                }
            ],
        },
    )
    assert created.status_code == 200
    sid = created.json()["id"]
    assert not created.json()["isEnabled"]
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
    initial = {
        "role": "user",
        "content": (
            "You must call sum_numbers with a=2 and b=3. "
            "After the tool replies, answer exactly 5 without another tool call."
        ),
    }
    tool = {
        "type": "function",
        "name": "sum_numbers",
        "description": "Add two numbers",
        "parameters": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
            "additionalProperties": False,
        },
    }
    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "trae/GPT-6-Astra",
            "input": [initial],
            "stream": True,
            "tools": [tool],
            "tool_choice": "auto",
        },
    )
    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    finals = [e["response"] for e in events if e.get("type") == "response.completed"]
    assert finals, [e for e in events if e.get("type") in ("response.failed", "error")]
    final = finals[0]
    call = next(item for item in final["output"] if item["type"] == "function_call")
    assert call["name"] == "sum_numbers" and json.loads(call["arguments"]) == {"a": 2, "b": 3}
    follow = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "trae/GPT-6-Astra",
            "stream": True,
            "input": [
                initial,
                *final["output"],
                {"type": "function_call_output", "call_id": call["call_id"], "output": "5"},
            ],
        },
    )
    assert follow.status_code == 200 and "response.completed" in follow.text
    follow_events = [
        json.loads(line[6:]) for line in follow.text.splitlines() if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    completed = next(e["response"] for e in follow_events if e.get("type") == "response.completed")
    answer = "".join(
        c.get("text", "")
        for item in completed["output"]
        if item["type"] == "message"
        for c in item["content"]
        if c["type"] == "output_text"
    )
    assert answer.strip() == "5"
    listed = (await async_client.get("/api/model-sources/")).json()["sources"][0]
    usage = listed["companyStatus"]["observedUsage"]
    assert usage["requests"] == 2 and usage["inputTokens"] > 0 and usage["outputTokens"] > 0
    assert listed["companyStatus"]["remaining"] is None
