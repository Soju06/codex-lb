"""Deterministic Responses origin used only for network-parity probes.

Launch the loopback fixture behind a TLS/HTTP-capable reverse capture process::

    uv run python -m scripts.traffic_analysis.origin_fixture --port 19090

The fixture never contacts an upstream service, validates no credential, and
never reflects request content. The reverse capture boundary is responsible
for TLS, HTTP/2, and privacy-safe source observation.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import ipaddress
import itertools
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import uvicorn
import zstandard as zstd
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

MAX_REQUEST_BYTES = 1024 * 1024
MAX_WEBSOCKET_MESSAGE_BYTES = 1024 * 1024
PROBE_MODEL = "gpt-5.6-luna"
DEFAULT_FAILURE_DELAY_SECONDS = 30.0
FAILURE_SCENARIOS = frozenset(
    {
        "success",
        "http_429",
        "http_503",
        "http_timeout",
        "sse_incomplete",
        "websocket_reject",
        "websocket_incomplete",
    }
)

app = FastAPI(
    title="Codex traffic parity origin fixture",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.failure_scenario = "success"
app.state.failure_delay_seconds = DEFAULT_FAILURE_DELAY_SECONDS

_response_numbers = itertools.count(1)


def _probe_model() -> dict[str, Any]:
    return {
        "slug": PROBE_MODEL,
        "display_name": "GPT-5.6-Luna Origin Probe",
        "description": "Deterministic traffic-parity fixture model.",
        "base_instructions": "",
        "context_window": 272_000,
        "max_context_window": 272_000,
        "input_modalities": ["text", "image"],
        "supported_reasoning_levels": [
            {"effort": effort, "description": f"{effort} probe effort"}
            for effort in ("low", "medium", "high", "xhigh", "max")
        ],
        "default_reasoning_level": "low",
        "supports_reasoning_summaries": True,
        "support_verbosity": True,
        "default_verbosity": "low",
        "prefer_websockets": True,
        "supports_parallel_tool_calls": True,
        "supported_in_api": True,
        "minimal_client_version": "0.144.0",
        "priority": 1,
        "available_in_plans": ["pro"],
        "shell_type": "shell_command",
        "visibility": "list",
        "truncation_policy": {"mode": "tokens", "limit": 10_000},
        "experimental_supported_tools": [],
        "tool_mode": "code_mode_only",
        "multi_agent_version": "v1",
        "use_responses_lite": True,
        "default_reasoning_summary": "none",
        "reasoning_summary_format": "experimental",
        "supports_search_tool": True,
        "service_tiers": [],
        "additional_speed_tiers": [],
    }


def _response(response_id: str, *, status: str) -> dict[str, Any]:
    completed = status == "completed"
    return {
        "id": response_id,
        "object": "response",
        "created_at": 0,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": PROBE_MODEL,
        "output": [],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": "low", "summary": None},
        "store": False,
        "temperature": 1.0,
        "text": {"format": {"type": "text"}},
        "tool_choice": "auto",
        "tools": [],
        "top_p": 1.0,
        "truncation": "disabled",
        "usage": (
            {
                "input_tokens": 1,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 1,
            }
            if completed
            else None
        ),
    }


def _response_events() -> list[dict[str, Any]]:
    response_id = f"resp_origin_probe_{next(_response_numbers)}"
    return [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": _response(response_id, status="in_progress"),
        },
        {
            "type": "response.completed",
            "sequence_number": 1,
            "response": _response(response_id, status="completed"),
        },
    ]


def _decode_request_body(body: bytes, content_encoding: str | None) -> bytes:
    encoding = (content_encoding or "identity").strip().casefold()
    if encoding == "identity":
        return body
    if encoding != "zstd":
        raise HTTPException(status_code=415, detail="unsupported request content encoding")
    try:
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(body)) as reader:
            decoded = reader.read(MAX_REQUEST_BYTES + 1)
    except (OSError, zstd.ZstdError) as exc:
        raise HTTPException(status_code=400, detail="request body is not valid zstd") from exc
    if len(decoded) > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="decoded request body exceeds origin probe limit")
    return decoded


async def _read_request_json(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="request body exceeds origin probe limit")
    decoded = _decode_request_body(bytes(body), request.headers.get("content-encoding"))
    try:
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="request body must be a JSON object") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload


async def _sse(events: Sequence[dict[str, Any]]) -> AsyncIterator[bytes]:
    for event in events:
        yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n".encode()


async def _incomplete_sse(events: Sequence[dict[str, Any]]) -> AsyncIterator[bytes]:
    """Emit a valid prefix and end before a terminal Responses event."""

    if events:
        yield f"data: {json.dumps(events[0], separators=(',', ':'))}\n\n".encode()


def _failure_response(status_code: int) -> JSONResponse:
    return JSONResponse(
        {
            "error": {
                "type": "origin_probe_error",
                "code": f"origin_probe_http_{status_code}",
                "message": "controlled origin failure",
            }
        },
        status_code=status_code,
        headers={"Retry-After": "1"} if status_code == 429 else None,
    )


def configure_failure_scenario(
    fixture_app: FastAPI,
    scenario: str,
    *,
    delay_seconds: float = DEFAULT_FAILURE_DELAY_SECONDS,
) -> None:
    """Apply process-owned failure configuration to a fixture app."""

    if scenario not in FAILURE_SCENARIOS:
        raise ValueError(f"failure scenario must be one of {sorted(FAILURE_SCENARIOS)}")
    if not 0 < delay_seconds <= 300:
        raise ValueError("failure delay seconds must be greater than 0 and no more than 300")
    fixture_app.state.failure_scenario = scenario
    fixture_app.state.failure_delay_seconds = float(delay_seconds)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "codex-traffic-origin-fixture"}


@app.get("/models")
@app.get("/v1/models")
@app.get("/codex/models")
@app.get("/backend-api/codex/models")
async def models() -> dict[str, list[dict[str, Any]]]:
    return {"models": [_probe_model()]}


@app.post("/v1/responses")
@app.post("/codex/responses")
@app.post("/backend-api/codex/responses")
async def responses(request: Request) -> Response:
    payload = await _read_request_json(request)
    scenario = str(request.app.state.failure_scenario)
    if scenario == "http_429":
        return _failure_response(429)
    if scenario == "http_503":
        return _failure_response(503)
    if scenario == "http_timeout":
        await asyncio.sleep(float(request.app.state.failure_delay_seconds))
    events = _response_events()
    if payload.get("stream") is True:
        return StreamingResponse(
            _incomplete_sse(events) if scenario == "sse_incomplete" else _sse(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(events[-1]["response"])


def _websocket_error(message: str) -> dict[str, Any]:
    return {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "invalid_origin_probe_request",
            "message": message,
        },
    }


async def _websocket_payload(websocket: WebSocket) -> dict[str, Any] | None:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        return None
    raw = message.get("text")
    if raw is None and (binary := message.get("bytes")) is not None:
        if len(binary) > MAX_WEBSOCKET_MESSAGE_BYTES:
            await websocket.close(code=1009, reason="origin probe frame limit")
            return None
        try:
            raw = binary.decode("utf-8")
        except UnicodeDecodeError:
            await websocket.send_json(_websocket_error("message must be UTF-8 JSON"))
            return {}
    if raw is None:
        await websocket.send_json(_websocket_error("message must be text or binary JSON"))
        return {}
    if len(raw.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
        await websocket.close(code=1009, reason="origin probe frame limit")
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.send_json(_websocket_error("message must be a JSON object"))
        return {}
    if not isinstance(payload, dict):
        await websocket.send_json(_websocket_error("message must be a JSON object"))
        return {}
    return payload


@app.websocket("/v1/responses")
@app.websocket("/codex/responses")
@app.websocket("/backend-api/codex/responses")
async def responses_websocket(websocket: WebSocket) -> None:
    scenario = str(websocket.app.state.failure_scenario)
    if scenario == "websocket_reject":
        await websocket.close(code=1013, reason="controlled origin handshake rejection")
        return
    await websocket.accept()
    try:
        while True:
            payload = await _websocket_payload(websocket)
            if payload is None:
                return
            if payload.get("type") != "response.create":
                if payload:
                    await websocket.send_json(_websocket_error("message type must be response.create"))
                continue
            events = _response_events()
            if scenario == "websocket_incomplete":
                await websocket.send_json(events[0])
                await websocket.close(code=1011, reason="controlled origin incomplete response")
                return
            for event in events:
                await websocket.send_json(event)
    except WebSocketDisconnect:
        return


def _loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_bind(host: str, *, allow_public_bind: bool) -> None:
    if not _loopback_host(host) and not allow_public_bind:
        raise ValueError("non-loopback origin fixture bind requires --allow-public-bind")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--allow-public-bind", action="store_true")
    parser.add_argument("--failure-scenario", choices=sorted(FAILURE_SCENARIOS), default="success")
    parser.add_argument(
        "--failure-delay-seconds",
        type=float,
        default=DEFAULT_FAILURE_DELAY_SECONDS,
        help="Finite delay used by the http_timeout scenario (0 < value <= 300)",
    )
    args = parser.parse_args(argv)
    try:
        validate_bind(args.host, allow_public_bind=args.allow_public_bind)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        configure_failure_scenario(
            app,
            args.failure_scenario,
            delay_seconds=args.failure_delay_seconds,
        )
    except ValueError as exc:
        parser.error(str(exc))
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        proxy_headers=False,
        forwarded_allow_ips="",
        server_header=False,
        ws_max_size=MAX_WEBSOCKET_MESSAGE_BYTES,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
