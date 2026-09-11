"""Explicit opt-in live probe; uses only the suite's isolated DB and synthetic inputs."""

from __future__ import annotations

import json
import os

import pytest

from app.modules.model_sources.llmbox import LLMBOX_BASE_URL

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CODEX_LB_TEST_LLMBOX_LIVE") != "1", reason="Explicit live LLMBox opt-in required"
    ),
]


@pytest.mark.asyncio
async def test_live_company_responses_tool_roundtrip(async_client):
    created = await async_client.post(
        "/api/model-sources/",
        json={
            "kind": "llmbox",
            "name": "Isolated live probe",
            "baseUrl": LLMBOX_BASE_URL,
            "supportsResponses": True,
            "supportsChatCompletions": False,
            "timeoutSeconds": 90,
            "models": [
                {
                    "model": "auto-max",
                    "supportsTools": True,
                    "supportsStreaming": True,
                    "rawMetadataJson": json.dumps({"supports_reasoning": True}),
                }
            ],
        },
    )
    assert created.status_code == 200
    sid = created.json()["id"]
    assert not created.json()["isEnabled"]
    await async_client.patch(f"/api/model-sources/{sid}", json={"isEnabled": True})
    initial = {"role": "user", "content": "Call sum_numbers with a=2 and b=3."}
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
            "model": "auto-max",
            "input": [initial],
            "stream": True,
            "max_output_tokens": 256,
            "tools": [tool],
            "tool_choice": {"type": "function", "name": "sum_numbers"},
        },
    )
    assert response.status_code == 200
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line[6:] != "[DONE]"
    ]
    final = next(e["response"] for e in events if e.get("type") == "response.completed")
    call = next(item for item in final["output"] if item["type"] == "function_call")
    assert call["name"] == "sum_numbers" and json.loads(call["arguments"]) == {"a": 2, "b": 3}
    follow = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "auto-max",
            "stream": True,
            "max_output_tokens": 128,
            "input": [
                initial,
                *final["output"],
                {"type": "function_call_output", "call_id": call["call_id"], "output": "5"},
            ],
        },
    )
    assert follow.status_code == 200 and "response.completed" in follow.text
    assert "5" in follow.text
    listed = (await async_client.get("/api/model-sources/")).json()["sources"][0]
    usage = listed["companyStatus"]["observedUsage"]
    assert usage["requests"] == 2 and usage["inputTokens"] > 0 and usage["outputTokens"] > 0
    assert listed["companyStatus"]["remaining"] is None
