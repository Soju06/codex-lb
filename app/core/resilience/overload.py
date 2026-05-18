from __future__ import annotations

import json
from collections.abc import Mapping

from starlette.types import Receive, Send

from app.core.errors import OpenAIErrorEnvelope, openai_error

LOCAL_OVERLOAD_CODE = "proxy_overloaded"
LOCAL_OVERLOAD_RETRY_AFTER_SECONDS = "5"


def local_overload_error(message: str) -> OpenAIErrorEnvelope:
    return openai_error(LOCAL_OVERLOAD_CODE, message, error_type="rate_limit_error")


def local_unavailable_error(message: str) -> OpenAIErrorEnvelope:
    return openai_error("proxy_unavailable", message, error_type="server_error")


def is_local_overload_error_code(code: str | None) -> bool:
    return code == LOCAL_OVERLOAD_CODE


def merge_retry_after_headers(
    headers: Mapping[str, str] | None = None,
    *,
    retry_after: str = LOCAL_OVERLOAD_RETRY_AFTER_SECONDS,
) -> dict[str, str]:
    merged = dict(headers or {})
    merged.setdefault("Retry-After", retry_after)
    return merged


def is_proxy_path(path: str) -> bool:
    return path.startswith("/v1/") or path.startswith("/backend-api/")


async def send_json_http_response(
    send: Send,
    *,
    status_code: int,
    payload: object,
    headers: Mapping[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    encoded_headers = [(b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        encoded_headers.append((key.lower().encode("ascii"), value.encode("utf-8")))
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": encoded_headers,
        }
    )
    await send({"type": "http.response.body", "body": body})


async def deny_websocket_with_http_response(
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    payload: object,
    headers: Mapping[str, str] | None = None,
) -> None:
    try:
        event = await receive()
        if event.get("type") != "websocket.connect":
            return
    except Exception:
        return
    body = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    encoded_headers = [(b"content-type", b"application/json")]
    for key, value in (headers or {}).items():
        encoded_headers.append((key.lower().encode("ascii"), value.encode("utf-8")))
    await send(
        {
            "type": "websocket.http.response.start",
            "status": status_code,
            "headers": encoded_headers,
        }
    )
    await send({"type": "websocket.http.response.body", "body": body})
