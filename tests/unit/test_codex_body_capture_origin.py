"""The capture origin itself, driven without ``codex``, ``unshare`` or uvicorn (#2123).

The guard tests cover the refusals. This module covers the thing that actually
records the evidence, because two claims about it were untrue and unobservable
from pure functions:

* ``--transport websocket`` was advertised in ``TRANSPORTS``, in the argparse
  choices, in the generated ``supports_websockets = true`` provider config and
  in the proposal, but the origin registered no websocket route at all, so a
  websocket run could not capture a body.
* The manifest recorded the *requested* transport, so a run that fell back to
  HTTP would claim a websocket capture.

Every frame shape here is the one real codex-cli 0.154.0 sends: the websocket
lane opens with a ``generate: false`` prewarm whose ``input`` is empty and only
then sends the turn, carrying ``previous_response_id`` for the primed response.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute

from scripts.traffic_analysis.codex_body_capture import (
    WEBSOCKET_TURN_FRAME_TYPE,
    CaptureTarget,
    _build_origin,
    is_turn_body,
)

pytestmark = pytest.mark.unit

_STARTED_AT = datetime(2026, 9, 11, 17, 29, 52, tzinfo=UTC)
_STAMP = "20260911T172952Z"

_TURN_INPUT: list[dict[str, Any]] = [
    {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "prefix"}]},
    {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Return exactly CAPTURE_OK."}]},
]


def _app(tmp_path: Path, *, slug: str, transport: str) -> tuple[FastAPI, list[dict[str, Any]], Path]:
    catalog = tmp_path / "models_cache.json"
    catalog.write_text(json.dumps({"models": [{"slug": slug}]}), encoding="utf-8")
    destination = tmp_path / "captures"
    destination.mkdir()
    app, captured = _build_origin(catalog, destination, _STARTED_AT)
    app.state.target = CaptureTarget(model_slug=slug, transport=transport)
    return app, captured, destination


def _origin(tmp_path: Path, *, slug: str, transport: str) -> tuple[TestClient, list[dict[str, Any]], Path]:
    app, captured, destination = _app(tmp_path, slug=slug, transport=transport)
    return TestClient(app), captured, destination


def _turn_frame(slug: str) -> dict[str, Any]:
    return {
        "type": WEBSOCKET_TURN_FRAME_TYPE,
        "model": slug,
        "instructions": "base",
        "input": _TURN_INPUT,
        "generate": None,
        "previous_response_id": "resp_origin_probe_1",
        "stream": True,
        "store": False,
    }


def _prewarm_frame(slug: str) -> dict[str, Any]:
    return {
        "type": WEBSOCKET_TURN_FRAME_TYPE,
        "model": slug,
        "instructions": "base",
        "input": [],
        "generate": False,
        "stream": True,
        "store": False,
    }


# --- is_turn_body ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"input": _TURN_INPUT}, True),
        ({"input": "a bare string turn"}, True),
        ({"input": [], "generate": False}, False),
        ({"input": _TURN_INPUT, "generate": False}, False),
        ({"input": []}, False),
        ({}, False),
    ],
)
def test_a_prewarm_frame_is_not_a_turn(payload: dict[str, Any], expected: bool) -> None:
    """``generate: false`` is production's own prewarm marker; an empty transcript is no turn."""

    assert is_turn_body(payload) is expected


# --- transports the origin serves -----------------------------------------------------


def test_the_origin_serves_both_transports_codex_can_choose(tmp_path: Path) -> None:
    """The provider config only *offers* websockets; the client picks, so both must exist."""

    app, _captured, _destination = _app(tmp_path, slug="gpt-5.5", transport="websocket")

    websocket_paths = {route.path for route in app.routes if isinstance(route, WebSocketRoute)}

    assert websocket_paths == {"/v1/responses", "/codex/responses", "/backend-api/codex/responses"}


def test_a_websocket_run_captures_the_turn_and_not_the_prewarm(tmp_path: Path) -> None:
    client, captured, destination = _origin(tmp_path, slug="gpt-5.5", transport="websocket")

    with client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text(json.dumps(_prewarm_frame("gpt-5.5")))
        websocket.send_text(json.dumps(_turn_frame("gpt-5.5")))
        assert websocket.receive_json()["type"]

    body_path = destination / f"body-gpt-5.5-websocket-{_STAMP}.json"
    persisted = json.loads(body_path.read_text(encoding="utf-8"))
    assert persisted["input"] == _TURN_INPUT
    assert (destination / f"headers-gpt-5.5-websocket-{_STAMP}.json").is_file()
    assert [entry.get("prewarm", False) for entry in captured] == [True, False]
    turn = next(entry for entry in captured if not entry["extra_turn"])
    assert turn["transport"] == "websocket"
    assert turn["summary"]["item_sequence"] == ["message/developer", "message/user"]


def test_the_prewarm_frame_is_kept_under_its_own_name(tmp_path: Path) -> None:
    """It is not noise: on the Lite websocket lane the prewarm carries the tool bundle.

    Measured against 0.154.0 — the `gpt-5.6-sol` websocket turn frame is 8 KB
    with no `additional_tools` item at all, because the bundle travelled in the
    prewarm. Discarding it would lose the tool surface the portability verdict
    is about.
    """

    client, captured, destination = _origin(tmp_path, slug="gpt-5.6-sol", transport="websocket")
    bundle: list[dict[str, Any]] = [{"type": "additional_tools", "role": "developer"}]
    prewarm = _prewarm_frame("gpt-5.6-sol") | {"input": bundle}

    with client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text(json.dumps(prewarm))
        websocket.send_text(json.dumps(_turn_frame("gpt-5.6-sol")))
        assert websocket.receive_json()["type"]

    prewarm_path = destination / f"prewarm-gpt-5.6-sol-websocket-{_STAMP}.json"
    assert json.loads(prewarm_path.read_text(encoding="utf-8"))["input"] == bundle
    record = next(entry for entry in captured if entry.get("prewarm"))
    assert record["prewarm_summary"]["item_sequence"] == ["additional_tools/developer"]
    assert record["extra_turn"] is True  # never mistaken for the captured body
    assert "body" not in record


def test_the_websocket_frame_is_persisted_verbatim_envelope_included(tmp_path: Path) -> None:
    """The frame *is* the request body on this transport; the capture rewrites no bytes."""

    client, _captured, destination = _origin(tmp_path, slug="gpt-5.5", transport="websocket")
    frame = json.dumps(_turn_frame("gpt-5.5"))

    with client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text(frame)
        assert websocket.receive_json()["type"]

    body_path = destination / f"body-gpt-5.5-websocket-{_STAMP}.json"
    assert body_path.read_text(encoding="utf-8") == frame


def test_a_non_turn_frame_type_is_ignored(tmp_path: Path) -> None:
    client, captured, destination = _origin(tmp_path, slug="gpt-5.5", transport="websocket")

    with client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text(json.dumps({"type": "response.cancel"}))
        websocket.send_text(json.dumps(_turn_frame("gpt-5.5")))
        assert websocket.receive_json()["type"]

    assert sorted(path.name for path in destination.glob("body-*.json")) == [f"body-gpt-5.5-websocket-{_STAMP}.json"]
    assert len(captured) == 1


def test_an_http_body_is_recorded_as_http_even_when_websocket_was_requested(tmp_path: Path) -> None:
    """A manifest that echoed ``--transport`` would claim a websocket capture on fallback."""

    client, captured, destination = _origin(tmp_path, slug="gpt-5.5", transport="websocket")

    response = client.post("/v1/responses", json={"model": "gpt-5.5", "input": _TURN_INPUT, "stream": False})

    assert response.status_code == 200
    assert captured[0]["transport"] == "http"
    assert (destination / f"body-gpt-5.5-http-{_STAMP}.json").is_file()
    assert not (destination / f"body-gpt-5.5-websocket-{_STAMP}.json").exists()


def test_a_second_turn_never_overwrites_the_first_capture(tmp_path: Path) -> None:
    client, captured, destination = _origin(tmp_path, slug="gpt-5.5", transport="websocket")
    first = _turn_frame("gpt-5.5")
    second = _turn_frame("gpt-5.5") | {"instructions": "a later turn"}

    with client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text(json.dumps(first))
        assert websocket.receive_json()["type"]
        websocket.send_text(json.dumps(second))
        assert websocket.receive_json()["type"]

    body_path = destination / f"body-gpt-5.5-websocket-{_STAMP}.json"
    assert json.loads(body_path.read_text(encoding="utf-8"))["instructions"] == "base"
    assert [entry["extra_turn"] for entry in captured] == [False, True]
