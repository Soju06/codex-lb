from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from app.core.auth.anthropic_credentials import (
    AnthropicCredentials,
    refresh_anthropic_access_token,
    resolve_anthropic_credentials,
)
from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.types import JsonValue

from .anthropic_proxy import AnthropicProxyError, anthropic_error_payload

_IGNORE_INBOUND_HEADERS = {
    "authorization",
    "host",
    "content-length",
    "x-api-key",
    "transfer-encoding",
    "connection",
}

_CACHE_TTL_SECONDS = 900
_MAX_EVENT_BYTES = 2 * 1024 * 1024


@dataclass(slots=True)
class _DetectedCliData:
    headers: dict[str, str]
    body_json: dict[str, Any] | None
    captured_at: float


_detected_cli_cache: _DetectedCliData | None = None
_detect_lock = asyncio.Lock()


async def create_message(
    payload: dict[str, JsonValue],
    headers: Mapping[str, str],
    *,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> dict[str, JsonValue]:
    request_payload = _prepare_payload(payload)
    creds = await _resolve_valid_credentials()
    inbound_headers = _filter_inbound_headers(headers)
    cli_data = await _get_detected_cli_data()
    if cli_data is not None:
        _merge_cli_headers(inbound_headers, cli_data.headers)
        _inject_system_prompt(request_payload, cli_data.body_json)

    request_headers = _build_request_headers(
        inbound_headers,
        access_token=creds.bearer_token,
        stream=False,
    )
    response_payload, status_code = await _request_json(
        request_payload,
        request_headers,
        base_url=base_url,
        session=session,
        creds=creds,
    )
    if status_code >= 400:
        raise AnthropicProxyError(status_code, _ensure_error_payload(response_payload, status_code))
    return response_payload


async def stream_messages(
    payload: dict[str, JsonValue],
    headers: Mapping[str, str],
    *,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
) -> AsyncIterator[str]:
    request_payload = _prepare_payload(payload)
    request_payload["stream"] = True

    creds = await _resolve_valid_credentials()
    inbound_headers = _filter_inbound_headers(headers)
    cli_data = await _get_detected_cli_data()
    if cli_data is not None:
        _merge_cli_headers(inbound_headers, cli_data.headers)
        _inject_system_prompt(request_payload, cli_data.body_json)

    request_headers = _build_request_headers(
        inbound_headers,
        access_token=creds.bearer_token,
        stream=True,
    )

    async for block in _stream_request(
        request_payload,
        request_headers,
        base_url=base_url,
        session=session,
        creds=creds,
    ):
        yield block


def _prepare_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    data = dict(payload)
    if data.get("temperature") is None:
        data.pop("temperature", None)
    if "temperature" in data and "top_p" in data:
        data.pop("top_p", None)
    return data


def _filter_inbound_headers(headers: Mapping[str, str]) -> dict[str, str]:
    filtered: dict[str, str] = {}
    for key, value in headers.items():
        lower = key.lower()
        if lower in _IGNORE_INBOUND_HEADERS:
            continue
        if lower.startswith("x-forwarded-"):
            continue
        if lower.startswith("cf-"):
            continue
        filtered[key] = value
    return filtered


def _build_request_headers(
    headers: dict[str, str],
    *,
    access_token: str,
    stream: bool,
) -> dict[str, str]:
    settings = get_settings()
    request_headers = dict(headers)
    request_headers["Authorization"] = f"Bearer {access_token}"
    request_headers["Content-Type"] = "application/json"
    request_headers["Accept"] = "text/event-stream" if stream else "application/json"
    request_headers["anthropic-version"] = settings.anthropic_api_version
    beta = settings.anthropic_api_beta
    if beta and beta.strip():
        request_headers["anthropic-beta"] = beta.strip()
    return request_headers


async def _resolve_valid_credentials() -> AnthropicCredentials:
    credentials = await resolve_anthropic_credentials()
    if credentials is None:
        raise AnthropicProxyError(
            503,
            anthropic_error_payload(
                "api_error",
                "Anthropic credentials not found. Set "
                "CODEX_LB_ANTHROPIC_USAGE_BEARER_TOKEN or configure Claude credentials.",
            ),
        )

    if _is_token_expiring_soon(credentials):
        refreshed = await refresh_anthropic_access_token(credentials)
        if refreshed is not None:
            return refreshed
    return credentials


def _is_token_expiring_soon(credentials: AnthropicCredentials) -> bool:
    expires_at_ms = credentials.expires_at_ms
    if expires_at_ms is None:
        return False
    now_ms = int(time.time() * 1000)
    return expires_at_ms <= now_ms + 60_000


async def _request_json(
    payload: dict[str, JsonValue],
    headers: dict[str, str],
    *,
    base_url: str | None,
    session: aiohttp.ClientSession | None,
    creds: AnthropicCredentials,
) -> tuple[dict[str, JsonValue], int]:
    response, status = await _request_json_once(payload, headers, base_url=base_url, session=session)
    if creds.refresh_token and _should_attempt_oauth_refresh(status, response):
        refreshed = await refresh_anthropic_access_token(creds)
        if refreshed is not None:
            headers = dict(headers)
            headers["Authorization"] = f"Bearer {refreshed.bearer_token}"
            response, status = await _request_json_once(payload, headers, base_url=base_url, session=session)
    return response, status


async def _request_json_once(
    payload: dict[str, JsonValue],
    headers: dict[str, str],
    *,
    base_url: str | None,
    session: aiohttp.ClientSession | None,
) -> tuple[dict[str, JsonValue], int]:
    settings = get_settings()
    timeout = aiohttp.ClientTimeout(total=settings.anthropic_api_timeout_seconds)
    client = session or get_http_client().session
    url = f"{(base_url or settings.anthropic_api_base_url).rstrip('/')}/v1/messages"

    try:
        async with client.post(url, json=payload, headers=headers, timeout=timeout) as response:
            data = await _safe_json(response)
            return data, response.status
    except aiohttp.ClientError as exc:
        raise AnthropicProxyError(502, anthropic_error_payload("api_error", f"Upstream unavailable: {exc}")) from exc
    except asyncio.TimeoutError as exc:
        raise AnthropicProxyError(504, anthropic_error_payload("api_error", "Anthropic API timeout")) from exc


async def _stream_request(
    payload: dict[str, JsonValue],
    headers: dict[str, str],
    *,
    base_url: str | None,
    session: aiohttp.ClientSession | None,
    creds: AnthropicCredentials,
) -> AsyncIterator[str]:
    try:
        async for block in _stream_request_once(payload, headers, base_url=base_url, session=session):
            yield block
        return
    except AnthropicProxyError as exc:
        if not creds.refresh_token or not _should_attempt_oauth_refresh(exc.status_code, exc.payload):
            raise

    refreshed = await refresh_anthropic_access_token(creds)
    if refreshed is None:
        raise AnthropicProxyError(
            401,
            anthropic_error_payload("authentication_error", "Anthropic authentication failed"),
        )

    retry_headers = dict(headers)
    retry_headers["Authorization"] = f"Bearer {refreshed.bearer_token}"
    async for block in _stream_request_once(payload, retry_headers, base_url=base_url, session=session):
        yield block


async def _stream_request_once(
    payload: dict[str, JsonValue],
    headers: dict[str, str],
    *,
    base_url: str | None,
    session: aiohttp.ClientSession | None,
) -> AsyncIterator[str]:
    settings = get_settings()
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=settings.anthropic_api_timeout_seconds, sock_read=None)
    client = session or get_http_client().session
    url = f"{(base_url or settings.anthropic_api_base_url).rstrip('/')}/v1/messages"

    try:
        async with client.post(url, json=payload, headers=headers, timeout=timeout) as response:
            if response.status >= 400:
                data = await _safe_json(response)
                raise AnthropicProxyError(response.status, _ensure_error_payload(data, response.status))

            async for event_block in _iter_sse_event_blocks(response):
                if event_block.strip():
                    yield event_block
    except AnthropicProxyError:
        raise
    except aiohttp.ClientError as exc:
        raise AnthropicProxyError(502, anthropic_error_payload("api_error", f"Upstream unavailable: {exc}")) from exc
    except asyncio.TimeoutError as exc:
        raise AnthropicProxyError(504, anthropic_error_payload("api_error", "Anthropic API timeout")) from exc


async def _safe_json(response: aiohttp.ClientResponse) -> dict[str, JsonValue]:
    try:
        data = await response.json(content_type=None)
    except Exception:
        text = (await response.text()).strip()
        if text:
            return anthropic_error_payload("api_error", text)
        return {}
    if isinstance(data, dict):
        return data
    return {}


def _ensure_error_payload(payload: dict[str, JsonValue], status_code: int) -> dict[str, JsonValue]:
    if payload.get("type") == "error" and isinstance(payload.get("error"), dict):
        return payload
    message: str = f"Anthropic API error ({status_code})"
    payload_message = payload.get("message")
    if isinstance(payload_message, str) and payload_message:
        message = payload_message
    return anthropic_error_payload("api_error", message)


def _should_attempt_oauth_refresh(status_code: int, payload: dict[str, JsonValue]) -> bool:
    if status_code == 401:
        return True
    if status_code != 403:
        return False

    error = payload.get("error")
    if not isinstance(error, dict):
        return False
    error_type = error.get("type")
    if error_type not in {"permission_error", "authentication_error"}:
        return False
    message = error.get("message")
    if not isinstance(message, str):
        return False
    lowered = message.casefold()
    return "oauth" in lowered and "revoked" in lowered


async def _iter_sse_event_blocks(response: aiohttp.ClientResponse) -> AsyncIterator[str]:
    buffer = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        if not chunk:
            continue
        buffer.extend(chunk)
        while True:
            separator = _find_separator(buffer)
            if separator is None:
                if len(buffer) > _MAX_EVENT_BYTES:
                    raise AnthropicProxyError(
                        502,
                        anthropic_error_payload("api_error", "Streaming event exceeded size limit"),
                    )
                break
            index, sep_len = separator
            end = index + sep_len
            raw = bytes(buffer[:end])
            del buffer[:end]
            yield raw.decode("utf-8", errors="replace")
    if buffer:
        yield bytes(buffer).decode("utf-8", errors="replace")


def _find_separator(buffer: bytes | bytearray) -> tuple[int, int] | None:
    pos_crlf = buffer.find(b"\r\n\r\n")
    pos_lf = buffer.find(b"\n\n")
    options: list[tuple[int, int]] = []
    if pos_crlf >= 0:
        options.append((pos_crlf, 4))
    if pos_lf >= 0:
        options.append((pos_lf, 2))
    if not options:
        return None
    return min(options, key=lambda item: item[0])


async def _get_detected_cli_data() -> _DetectedCliData | None:
    settings = get_settings()
    if not settings.anthropic_api_detect_cli_headers:
        return None

    global _detected_cli_cache
    cached = _detected_cli_cache
    now = time.monotonic()
    if cached is not None and now - cached.captured_at < _CACHE_TTL_SECONDS:
        return cached

    async with _detect_lock:
        cached = _detected_cli_cache
        now = time.monotonic()
        if cached is not None and now - cached.captured_at < _CACHE_TTL_SECONDS:
            return cached

        detected = await _detect_cli_headers_and_body()
        _detected_cli_cache = detected
        return detected


async def _detect_cli_headers_and_body() -> _DetectedCliData | None:
    cli_binary = _find_claude_binary()
    if cli_binary is None:
        return None

    captured_headers: dict[str, str] = {}
    captured_body: dict[str, Any] | None = None
    app = web.Application()

    async def handle_messages(request: web.Request) -> web.Response:
        nonlocal captured_headers, captured_body
        captured_headers = {
            key.lower(): value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "authorization", "x-api-key", "content-length"}
        }
        raw = await request.read()
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            decoded = None
        captured_body = decoded if isinstance(decoded, dict) else None
        return web.json_response(
            {
                "id": "msg_detect",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-20250514",
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    app.router.add_post("/v1/messages", handle_messages)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        await _run_claude_detection_command(cli_binary, port)
    finally:
        await runner.cleanup()

    if not captured_headers:
        return None
    return _DetectedCliData(
        headers=captured_headers,
        body_json=captured_body,
        captured_at=time.monotonic(),
    )


def _find_claude_binary() -> str | None:
    settings = get_settings()
    if settings.anthropic_sdk_cli_path:
        candidate = Path(settings.anthropic_sdk_cli_path).expanduser()
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)

    path = shutil_which("claude")
    return path


def shutil_which(binary: str) -> str | None:
    from shutil import which

    return which(binary)


async def _run_claude_detection_command(cli_binary: str, port: int) -> None:
    env = dict(os.environ)
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryDirectory(prefix="codex-lb-claude-detect-") as temp_dir:
        process = await asyncio.create_subprocess_exec(
            cli_binary,
            "test",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=temp_dir,
            env=env,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.wait()


def _merge_cli_headers(target: dict[str, str], cli_headers: dict[str, str]) -> None:
    blocked = {"authorization", "x-api-key", "host", "content-length"}
    for key, value in cli_headers.items():
        lower = key.lower()
        if lower in blocked:
            continue
        target[key] = value


def _inject_system_prompt(payload: dict[str, JsonValue], body_json: dict[str, Any] | None) -> None:
    if body_json is None:
        return
    settings = get_settings()
    mode = settings.anthropic_api_system_prompt_injection_mode
    if mode == "none":
        return

    detected_system = body_json.get("system")
    if not isinstance(detected_system, (str, list, dict)):
        return

    if mode == "minimal":
        if payload.get("system") is None:
            payload["system"] = detected_system
        return

    payload["system"] = detected_system
