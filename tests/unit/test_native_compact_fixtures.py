from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest

from app.core.clients.proxy import ProxyResponseError, SSEResponse, _compact_response_payload_from_sse

FIXTURES = Path(__file__).resolve().parents[2] / "crates/codex-lb-responses/tests/fixtures/compact-v1.json"
CASES = json.loads(FIXTURES.read_text())


class _FramedResponse:
    def __init__(self, blocks: list[str]) -> None:
        self.blocks = blocks
        self.content = self

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for block in self.blocks:
            yield block.encode()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_compact_fixture_matches_python_collector(case: dict[str, Any]) -> None:
    expected = case["expected"]
    response = cast(SSEResponse, _FramedResponse(case["blocks"]))
    if expected["kind"] == "invalid":
        with pytest.raises(ValueError, match=expected["message"]):
            await _compact_response_payload_from_sse(response, 1, 1024 * 1024)
    elif expected["kind"] == "terminal_error":
        with pytest.raises(ProxyResponseError):
            await _compact_response_payload_from_sse(response, 1, 1024 * 1024)
    else:
        assert await _compact_response_payload_from_sse(response, 1, 1024 * 1024) == expected["response"]
