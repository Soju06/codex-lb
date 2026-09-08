from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.clients import proxy

FIXTURES = Path(__file__).resolve().parents[2] / "crates/codex-lb-responses/tests/fixtures/stream-v1.json"
CASES = json.loads(FIXTURES.read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_stream_fixture_matches_python(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.errors.time.time", lambda: 1700000000)
    normalized = proxy._normalize_sse_event_block(case["block"])
    for expected in case["expected"]:
        text, kind = proxy._normalize_stream_payload_for_http_block(
            normalized, enforce_openai_sdk_contract=expected["sdk"]
        )
        assert (text, kind) == (expected["text"], expected["event_type"])
