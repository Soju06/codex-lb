from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
import zstandard as zstd
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from scripts.traffic_analysis import origin_fixture
from scripts.traffic_analysis.origin_fixture import (
    FAILURE_SCENARIOS,
    MAX_REQUEST_BYTES,
    MAX_WEBSOCKET_MESSAGE_BYTES,
    PROBE_MODEL,
    app,
    configure_failure_scenario,
    validate_bind,
)


@pytest.fixture(autouse=True)
def _reset_failure_scenario() -> Iterator[None]:
    configure_failure_scenario(app, "success")
    yield
    configure_failure_scenario(app, "success")


def test_origin_fixture_model_discovery_is_codex_compatible() -> None:
    with TestClient(app) as client:
        response = client.get("/backend-api/codex/models?client_version=0.150.1")

    assert response.status_code == 200
    model = response.json()["models"][0]
    assert model["slug"] == PROBE_MODEL
    assert model["prefer_websockets"] is True
    assert model["experimental_supported_tools"] == []
    assert model["truncation_policy"] == {"mode": "tokens", "limit": 10_000}


def test_origin_fixture_returns_json_without_reflecting_request_content() -> None:
    secret = "prompt-that-must-not-be-reflected"
    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            headers={"Authorization": "Bearer credential-that-must-not-be-reflected"},
            json={"model": "client-model", "input": secret, "stream": False},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["model"] == PROBE_MODEL
    serialized = response.text
    assert secret not in serialized
    assert "credential-that-must-not-be-reflected" not in serialized
    assert "client-model" not in serialized


def test_origin_fixture_returns_ordered_sse_terminal() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/backend-api/codex/responses",
            json={"model": PROBE_MODEL, "input": "private", "stream": True},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(block.removeprefix("data: ")) for block in response.text.strip().split("\n\n")]
    assert [event["type"] for event in events] == ["response.created", "response.completed"]
    assert events[0]["response"]["id"] == events[1]["response"]["id"]
    assert events[1]["response"]["status"] == "completed"
    assert "private" not in response.text


def test_origin_fixture_accepts_codex_zstd_request() -> None:
    body = json.dumps({"model": PROBE_MODEL, "input": "private", "stream": True}).encode()
    compressed = zstd.ZstdCompressor().compress(body)

    with TestClient(app) as client:
        response = client.post(
            "/backend-api/codex/responses",
            content=compressed,
            headers={"content-encoding": "zstd", "content-type": "application/json"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "response.completed" in response.text
    assert "private" not in response.text


def test_origin_fixture_keeps_multiple_websocket_turns_separate() -> None:
    with TestClient(app) as client, client.websocket_connect("/codex/responses") as websocket:
        event_types: list[str] = []
        response_ids: list[str] = []
        for _ in range(2):
            websocket.send_json({"type": "response.create", "model": PROBE_MODEL, "input": "private"})
            created = websocket.receive_json()
            completed = websocket.receive_json()
            event_types.extend((created["type"], completed["type"]))
            assert created["response"]["id"] == completed["response"]["id"]
            response_ids.append(completed["response"]["id"])

    assert event_types == ["response.created", "response.completed"] * 2
    assert len(set(response_ids)) == 2


@pytest.mark.parametrize("scenario,status_code", [("http_429", 429), ("http_503", 503)])
def test_origin_fixture_returns_controlled_http_failure(scenario: str, status_code: int) -> None:
    configure_failure_scenario(app, scenario)

    with TestClient(app) as client:
        response = client.post(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer credential-that-must-not-be-reflected"},
            json={"model": PROBE_MODEL, "input": "private", "stream": True},
        )

    assert response.status_code == status_code
    assert response.headers.get("retry-after") == ("1" if status_code == 429 else None)
    assert response.json()["error"]["code"] == f"origin_probe_http_{status_code}"
    assert "private" not in response.text
    assert "credential-that-must-not-be-reflected" not in response.text


def test_origin_fixture_delays_http_response() -> None:
    configure_failure_scenario(app, "http_timeout", delay_seconds=0.01)

    with TestClient(app) as client:
        response = client.post(
            "/backend-api/codex/responses",
            json={"model": PROBE_MODEL, "input": "private", "stream": False},
        )

    assert response.status_code == 200


def test_origin_fixture_ends_sse_without_terminal_event() -> None:
    configure_failure_scenario(app, "sse_incomplete")

    with TestClient(app) as client:
        response = client.post(
            "/backend-api/codex/responses",
            json={"model": PROBE_MODEL, "input": "private", "stream": True},
        )

    assert response.status_code == 200
    assert "response.created" in response.text
    assert "response.completed" not in response.text


def test_origin_fixture_rejects_websocket_handshake() -> None:
    configure_failure_scenario(app, "websocket_reject")

    with TestClient(app) as client, pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/backend-api/codex/responses"):
            pass


def test_origin_fixture_closes_websocket_without_terminal_event() -> None:
    configure_failure_scenario(app, "websocket_incomplete")

    with TestClient(app) as client, client.websocket_connect("/backend-api/codex/responses") as websocket:
        websocket.send_json({"type": "response.create", "model": PROBE_MODEL, "input": "private"})
        assert websocket.receive_json()["type"] == "response.created"
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1011


def test_origin_fixture_bounds_http_body() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            content=b"{" + (b"x" * MAX_REQUEST_BYTES),
            headers={"content-type": "application/json"},
        )

    assert response.status_code == 413


def test_origin_fixture_bounds_decoded_zstd_body() -> None:
    body = b'{"input":"' + (b"x" * MAX_REQUEST_BYTES) + b'"}'
    compressed = zstd.ZstdCompressor().compress(body)
    assert len(compressed) < MAX_REQUEST_BYTES

    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            content=compressed,
            headers={"content-encoding": "zstd", "content-type": "application/json"},
        )

    assert response.status_code == 413


@pytest.mark.parametrize(
    ("content", "content_encoding", "status_code"),
    [(b"not-zstd", "zstd", 400), (b"{}", "gzip", 415), (b"{}", "gzip, zstd", 415)],
)
def test_origin_fixture_rejects_invalid_content_encoding(
    content: bytes,
    content_encoding: str,
    status_code: int,
) -> None:
    with TestClient(app) as client:
        response = client.post(
            "/v1/responses",
            content=content,
            headers={"content-encoding": content_encoding, "content-type": "application/json"},
        )

    assert response.status_code == status_code


def test_origin_fixture_bounds_websocket_message() -> None:
    with TestClient(app) as client, client.websocket_connect("/v1/responses") as websocket:
        websocket.send_text("x" * (MAX_WEBSOCKET_MESSAGE_BYTES + 1))
        with pytest.raises(WebSocketDisconnect) as exc_info:
            websocket.receive_json()

    assert exc_info.value.code == 1009


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_origin_fixture_allows_loopback_bind(host: str) -> None:
    validate_bind(host, allow_public_bind=False)


def test_origin_fixture_requires_explicit_public_bind_acknowledgement() -> None:
    with pytest.raises(ValueError, match="--allow-public-bind"):
        validate_bind("0.0.0.0", allow_public_bind=False)

    validate_bind("0.0.0.0", allow_public_bind=True)


def test_origin_fixture_rejects_invalid_failure_configuration() -> None:
    with pytest.raises(ValueError, match="failure scenario"):
        configure_failure_scenario(app, "request-selected")
    with pytest.raises(ValueError, match="failure delay"):
        configure_failure_scenario(app, "http_timeout", delay_seconds=0)
    assert "success" in FAILURE_SCENARIOS


def test_origin_fixture_cli_rejects_public_bind_before_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    launched = False

    def fake_run(*_args: object, **_kwargs: object) -> None:
        nonlocal launched
        launched = True

    monkeypatch.setattr(origin_fixture.uvicorn, "run", fake_run)
    with pytest.raises(SystemExit) as exc_info:
        origin_fixture.main(["--host", "0.0.0.0"])

    assert exc_info.value.code == 2
    assert launched is False


def test_origin_fixture_cli_applies_operator_failure_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    launched: dict[str, object] = {}

    def fake_run(fixture_app: object, **kwargs: object) -> None:
        launched["app"] = fixture_app
        launched.update(kwargs)

    monkeypatch.setattr(origin_fixture.uvicorn, "run", fake_run)

    assert origin_fixture.main(["--failure-scenario", "http_timeout", "--failure-delay-seconds", "0.25"]) == 0
    assert launched["app"] is app
    assert launched["host"] == "127.0.0.1"
    assert app.state.failure_scenario == "http_timeout"
    assert app.state.failure_delay_seconds == 0.25
