from __future__ import annotations

import pytest

from app.modules.proxy.response_context_cache import ResponseContextCache
from app.modules.proxy.service import _extract_completion_context, _item_reference_id


def test_item_reference_id_detects_reference() -> None:
    item = {"type": "item_reference", "id": " rs_123 "}
    assert _item_reference_id(item) == "rs_123"


def test_item_reference_id_ignores_non_reference() -> None:
    assert _item_reference_id({"type": "message", "id": "rs_123"}) is None
    assert _item_reference_id({"type": "item_reference"}) is None


def test_extract_completion_context_reads_response_id_and_output_text() -> None:
    payload = {
        "type": "response.completed",
        "response": {
            "id": "resp_abc",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Hello world"}],
                }
            ],
        },
    }
    response_id, assistant_text = _extract_completion_context(payload)
    assert response_id == "resp_abc"
    assert assistant_text == "Hello world"


@pytest.mark.asyncio
async def test_response_context_cache_roundtrip() -> None:
    cache = ResponseContextCache(ttl_seconds=10, max_entries=10)
    await cache.put_context(
        "api_key:hash:abc",
        "resp_1",
        [{"role": "user", "content": "hello"}],
        "assistant-reply",
    )

    cached = await cache.get_context("api_key:hash:abc", "resp_1")
    assert cached is not None
    assert cached[0] == {"role": "user", "content": "hello"}
    assert cached[1] == {"role": "assistant", "content": "assistant-reply"}
