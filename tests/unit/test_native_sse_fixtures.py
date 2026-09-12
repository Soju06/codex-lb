"""The retained Python transport and Rust framer share one golden contract."""

import base64
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.clients.proxy import SSEResponse, _iter_sse_events
from app.core.clients.stream_errors import StreamEventTooLargeError

_FIXTURE = Path(__file__).resolve().parents[2] / "crates/codex-lb-protocol/tests/fixtures/sse-v1.json"
_CASES = json.loads(_FIXTURE.read_text(encoding="utf-8"))["cases"]


class _Content:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            for offset in range(0, len(chunk), size):
                yield chunk[offset : offset + size]


@pytest.mark.unit
@pytest.mark.parametrize("case", _CASES, ids=[case["name"] for case in _CASES])
async def test_shared_sse_fixture_matches_python_transport(case: dict) -> None:
    chunks = [base64.b64decode(chunk, validate=True) for chunk in case["chunks_base64"]]
    response = cast(SSEResponse, SimpleNamespace(content=_Content(chunks)))
    events: list[str] = []
    failure = None
    async with contextlib.aclosing(_iter_sse_events(response, 1, case["max_event_bytes"])) as stream:
        try:
            async for event in stream:
                events.append(event)
        except StreamEventTooLargeError as exc:
            failure = {"type": "event_too_large", "size_bytes": exc.size_bytes, "limit_bytes": exc.limit_bytes}
    assert events == case["events"]
    assert failure == case.get("failure")
