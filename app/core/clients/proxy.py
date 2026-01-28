from __future__ import annotations

import asyncio
from typing import AsyncIterator, Mapping, Protocol, TypeAlias

import aiohttp

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.errors import OpenAIErrorEnvelope, ResponseFailedEvent, openai_error, response_failed_event
from app.core.openai.models import OpenAIResponsePayload
from app.core.openai.parsing import parse_error_payload, parse_response_payload, parse_sse_event
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event

IGNORE_INBOUND_HEADERS = {"authorization", "chatgpt-account-id", "content-length", "host"}

_ERROR_TYPE_CODE_MAP = {
    "rate_limit_exceeded": "rate_limit_exceeded",
    "usage_not_included": "usage_not_included",
    "insufficient_quota": "insufficient_quota",
    "quota_exceeded": "quota_exceeded",
}


class StreamIdleTimeoutError(Exception):
    pass


class ErrorResponseProtocol(Protocol):
    status: int
    reason: str | None

    async def json(self, *, content_type: str | None = None) -> object: ...

    async def text(self, *, encoding: str | None = None, errors: str = "strict") -> str: ...


ErrorResponse: TypeAlias = aiohttp.ClientResponse | ErrorResponseProtocol


class ProxyResponseError(Exception):
    def __init__(self, status_code: int, payload: OpenAIErrorEnvelope) -> None:
        super().__init__(f"Proxy response error ({status_code})")
        self.status_code = status_code
        self.payload = payload


def filter_inbound_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if key.lower() not in IGNORE_INBOUND_HEADERS}


def _build_upstream_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    accept: str = "text/event-stream",
) -> dict[str, str]:
    headers = dict(inbound)
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = accept
    headers["Content-Type"] = "application/json"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    return headers


def _normalize_error_code(code: str | None, error_type: str | None) -> str:
    if code:
        normalized_code = code.lower()
        mapped = _ERROR_TYPE_CODE_MAP.get(normalized_code)
        return mapped or normalized_code
    normalized_type = error_type.lower() if error_type else None
    if normalized_type:
        mapped = _ERROR_TYPE_CODE_MAP.get(normalized_type)
        return mapped or normalized_type
    return "upstream_error"


async def _iter_sse_lines(
    resp: aiohttp.ClientResponse,
    idle_timeout_seconds: float,
) -> AsyncIterator[bytes]:
    while True:
        try:
            line = await asyncio.wait_for(
                resp.content.readline(),
                timeout=idle_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise StreamIdleTimeoutError() from exc
        if not line:
            break
        yield line


async def _error_event_from_response(resp: ErrorResponse) -> ResponseFailedEvent:
    fallback_message = f"Upstream error: HTTP {resp.status}"
    if resp.reason:
        fallback_message += f" {resp.reason}"
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        message = text.strip() or fallback_message
        return response_failed_event("upstream_error", message, response_id=get_request_id())

    if isinstance(data, dict):
        error = parse_error_payload(data)
        if error:
            payload = error.model_dump(exclude_none=True)
            event = response_failed_event(
                _normalize_error_code(payload.get("code"), payload.get("type")),
                payload.get("message", fallback_message),
                error_type=payload.get("type") or "server_error",
                response_id=get_request_id(),
                error_param=payload.get("param"),
            )
            for key in ("plan_type", "resets_at", "resets_in_seconds"):
                if key in payload:
                    event["response"]["error"][key] = payload[key]
            return event
        message = _extract_upstream_message(data)
        if message:
            return response_failed_event("upstream_error", message, response_id=get_request_id())
    return response_failed_event("upstream_error", fallback_message, response_id=get_request_id())


async def _error_payload_from_response(resp: ErrorResponse) -> OpenAIErrorEnvelope:
    fallback_message = f"Upstream error: HTTP {resp.status}"
    if resp.reason:
        fallback_message += f" {resp.reason}"
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        message = text.strip() or fallback_message
        return openai_error("upstream_error", message)

    if isinstance(data, dict):
        error = parse_error_payload(data)
        if error:
            return {"error": error.model_dump(exclude_none=True)}
        message = _extract_upstream_message(data)
        if message:
            return openai_error("upstream_error", message)
    return openai_error("upstream_error", fallback_message)


def _extract_upstream_message(data: dict) -> str | None:
    for key in ("message", "detail", "error"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


async def stream_responses(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    raise_for_status: bool = False,
    session: aiohttp.ClientSession | None = None,
) -> AsyncIterator[str]:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/codex/responses"
    upstream_headers = _build_upstream_headers(headers, access_token, account_id)
    timeout = aiohttp.ClientTimeout(
        total=None,
        sock_connect=settings.upstream_connect_timeout_seconds,
        sock_read=None,
    )

    seen_terminal = False
    client_session = session or get_http_client().session
    try:
        async with client_session.post(
            url,
            json=payload.to_payload(),
            headers=upstream_headers,
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                if raise_for_status:
                    error_payload = await _error_payload_from_response(resp)
                    raise ProxyResponseError(resp.status, error_payload)
                event = await _error_event_from_response(resp)
                yield format_sse_event(event)
                return

            async for raw_line in _iter_sse_lines(resp, settings.stream_idle_timeout_seconds):
                line = raw_line.decode("utf-8", errors="replace")
                event = parse_sse_event(line)
                if event:
                    event_type = event.type
                    if event_type in ("response.completed", "response.failed"):
                        seen_terminal = True
                yield line
    except ProxyResponseError:
        raise
    except StreamIdleTimeoutError:
        yield format_sse_event(
            response_failed_event(
                "stream_idle_timeout",
                "Upstream stream idle timeout",
                response_id=get_request_id(),
            ),
        )
        return
    except aiohttp.ClientError as exc:
        yield format_sse_event(
            response_failed_event("upstream_unavailable", str(exc), response_id=get_request_id()),
        )
        return
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield format_sse_event(response_failed_event("upstream_error", str(exc), response_id=get_request_id()))
        return

    if not seen_terminal:
        yield format_sse_event(
            response_failed_event(
                "stream_incomplete",
                "Upstream closed stream without completion",
                response_id=get_request_id(),
            ),
        )


async def compact_responses(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    session: aiohttp.ClientSession | None = None,
) -> OpenAIResponsePayload:
    settings = get_settings()
    upstream_base = settings.upstream_base_url.rstrip("/")
    url = f"{upstream_base}/codex/responses/compact"
    upstream_headers = _build_upstream_headers(
        headers,
        access_token,
        account_id,
        accept="application/json",
    )
    timeout = aiohttp.ClientTimeout(
        total=60,
        sock_connect=settings.upstream_connect_timeout_seconds,
        sock_read=60,
    )

    client_session = session or get_http_client().session
    try:
        async with client_session.post(
            url,
            json=payload.to_payload(),
            headers=upstream_headers,
            timeout=timeout,
        ) as resp:
            if resp.status >= 400:
                error_payload = await _error_payload_from_response(resp)
                raise ProxyResponseError(resp.status, error_payload)
            try:
                data = await resp.json(content_type=None)
            except Exception as exc:
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Invalid JSON from upstream"),
                ) from exc
            parsed = parse_response_payload(data)
            if parsed:
                return parsed
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "Unexpected upstream payload"),
            )
    except ProxyResponseError:
        raise
    except aiohttp.ClientError as exc:
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", str(exc)),
        ) from exc
