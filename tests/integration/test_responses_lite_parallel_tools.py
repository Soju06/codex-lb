from __future__ import annotations

import json

import pytest

from app.core.clients.proxy import CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY
from tests.integration.test_http_responses_bridge import (
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,  # noqa: F401
    _collect_sse_events,
)
from tests.integration.test_http_responses_bridge import (
    promotion_transport as promotion_transport,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses"])
@pytest.mark.parametrize("lite", [False, True])
async def test_routes_normalize_only_lite_parallel_calls(
    async_client, promotion_transport, path: str, lite: bool
) -> None:
    upstreams, raw_calls, _ = promotion_transport
    items = [{"role": "user", "content": "hello"}]
    if lite:
        items.insert(0, {"type": "additional_tools", "role": "developer", "tools": []})
    body = {
        "model": "gpt-5.4",
        "instructions": "",
        "stream": True,
        "input": items,
        "parallel_tool_calls": True,
        "prompt_cache_key": "lite-parallel-route",
        "reasoning": {"effort": "high"},
    }

    events = await _collect_sse_events(async_client, path, json_body=body)

    _assert_created_text_delta_completed(events)
    assert len(upstreams) == 1 and not raw_calls
    sent = json.loads(upstreams[0].sent_text[0])
    assert sent["parallel_tool_calls"] is (not lite)
    assert sent["prompt_cache_key"] == body["prompt_cache_key"]
    assert sent["input"] == items
    assert sent["reasoning"]["effort"] == "high"
    marker = sent.get("client_metadata", {}).get(CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY)
    assert marker == ("true" if lite else None)
