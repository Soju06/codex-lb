from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.modules.proxy._service.websocket.helpers import _websocket_response_id
from app.modules.proxy._service.websocket.mixin import _parse_upstream_websocket_text_frame

FIXTURES = Path(__file__).resolve().parents[2] / "crates/codex-lb-responses/tests/fixtures/websocket-routing-v1.json"
CASES = json.loads(FIXTURES.read_text())


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_routing_fixture_matches_python(case: dict[str, Any]) -> None:
    payload = json.loads(case["text"])
    assert _websocket_response_id(None, payload) == case["payload_response_id"]
    frame = _parse_upstream_websocket_text_frame(case["text"])
    assert frame.response_id == case["response_id"]
    assert frame.sequence_number == (json.loads(case["sequence_token"]) if case["sequence_token"] is not None else None)
