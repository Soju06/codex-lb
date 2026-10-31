"""One bounded, account-pinned completion test; no response content leaves this module."""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal, cast

import aiohttp

from app.core.clients.codex import CodexTransportError
from app.core.clients.proxy import (
    ProxyResponseError,
    StreamEventTooLargeError,
    StreamIdleTimeoutError,
    UpstreamProxyRouteTrace,
    override_stream_timeouts,
    stream_responses,
)
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIError
from app.core.openai.parsing import parse_sse_event
from app.core.openai.requests import ResponsesRequest
from app.core.runtime_logging import redact_rendered_log_text
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.db.models import Account
from app.db.session import get_background_session

COMPLETION_TIMEOUT_SECONDS = 30.0
COMPLETION_IDLE_TIMEOUT_SECONDS = 10.0
COMPLETION_MAX_EVENT_BYTES = 64 * 1024
COMPLETION_MAX_STREAM_BYTES = 256 * 1024
COMPLETION_MAX_ERROR_BYTES = 64 * 1024
COMPLETION_MAX_ERROR_MESSAGE_CHARS = 512
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_BARE_CREDENTIAL = re.compile(r"\b(?:sk-[A-Za-z0-9_-]+|eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b")
_URL = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^\s<>\"']+")


@dataclass(frozen=True, slots=True)
class CompletionProbeResult:
    http_status: int
    outcome: Literal["completed", "failed", "incomplete"]
    latency_ms: int
    error_code: str | None = None
    error_message: str | None = None


def _safe_error(
    error: OpenAIError | None, *, access_token: str, fallback_code: str, fallback_message: str
) -> tuple[str, str]:
    code = (error.code or error.type) if error is not None else None
    if code is None or not _SAFE_ERROR_CODE.fullmatch(code) or code == access_token:
        code = fallback_code
    message = (error.message or fallback_message) if error is not None else fallback_message
    if access_token:
        message = message.replace(access_token, "[REDACTED]")
    message = redact_rendered_log_text(message)
    message = _URL.sub("[REDACTED]", _BARE_CREDENTIAL.sub("[REDACTED]", message))
    message = " ".join(message.split())[:COMPLETION_MAX_ERROR_MESSAGE_CHARS]
    return code, message or fallback_message


async def send_completion_probe(
    account: Account, *, access_token: str, model: str, encryptor: TokenEncryptor
) -> CompletionProbeResult:
    started = time.monotonic()
    trace = UpstreamProxyRouteTrace()

    def result(
        outcome: Literal["completed", "failed", "incomplete"],
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> CompletionProbeResult:
        if trace.http_status is not None and trace.http_status >= 300:
            outcome = "failed"
            if error_code in (None, "probe_timeout", "stream_incomplete"):
                error_code = "upstream_http_error"
                error_message = "Upstream rejected the account test."
        return CompletionProbeResult(
            trace.http_status or 0, outcome, int((time.monotonic() - started) * 1000), error_code, error_message
        )

    try:
        async with asyncio.timeout(COMPLETION_TIMEOUT_SECONDS):
            async with get_background_session() as session:
                route = await resolve_upstream_route(
                    session, account_id=account.id, operation="account_probe", scope="account", encryptor=encryptor
                )
            payload = ResponsesRequest.model_validate(
                {
                    "model": model,
                    "instructions": "Respond with a single dot.",
                    "input": [{"role": "user", "content": [{"type": "input_text", "text": "."}]}],
                    "tools": [],
                    "parallel_tool_calls": False,
                    "stream": True,
                    "store": False,
                }
            )
            with override_stream_timeouts(
                total_timeout_seconds=COMPLETION_TIMEOUT_SECONDS,
                connect_timeout_seconds=COMPLETION_IDLE_TIMEOUT_SECONDS,
                idle_timeout_seconds=COMPLETION_IDLE_TIMEOUT_SECONDS,
            ):
                stream = stream_responses(
                    payload,
                    {},
                    access_token,
                    account.chatgpt_account_id,
                    raise_for_status=True,
                    upstream_stream_transport_override="http",
                    route=route,
                    route_trace=trace,
                    allow_direct_egress=route is None,
                    codex_installation_id=account.codex_installation_id,
                    codex_lb_account_id=account.id,
                    suppress_live_usage=True,
                    max_sse_event_bytes=COMPLETION_MAX_EVENT_BYTES,
                    error_body_max_bytes=COMPLETION_MAX_ERROR_BYTES,
                )
                stream_bytes = 0
                async with contextlib.aclosing(cast(AsyncGenerator[str, None], stream)) as events:
                    async for block in events:
                        stream_bytes += len(block.encode("utf-8"))
                        if stream_bytes > COMPLETION_MAX_STREAM_BYTES:
                            return result("incomplete", "probe_stream_limit", "Account test exceeded its stream limit.")
                        event = parse_sse_event(block)
                        if event is None:
                            continue
                        if (
                            event.type == "response.completed"
                            and trace.http_status is not None
                            and 200 <= trace.http_status < 300
                        ):
                            return result("completed")
                        if event.type == "response.incomplete":
                            return result(
                                "incomplete", "response_incomplete", "Upstream did not complete the account test."
                            )
                        if event.type in ("response.failed", "error"):
                            error = event.error or (event.response.error if event.response is not None else None)
                            code = error.code if error is not None else None
                            if code in {"stream_idle_timeout", "upstream_request_timeout"}:
                                return result(
                                    "incomplete", "probe_timeout", "Account test timed out before completion."
                                )
                            if code == "stream_event_too_large":
                                return result(
                                    "incomplete", "probe_stream_limit", "Account test exceeded its stream limit."
                                )
                            if code == "stream_incomplete":
                                return result(
                                    "incomplete", "stream_incomplete", "Account test stream ended before completion."
                                )
                            if code in {"upstream_error", "upstream_unavailable"}:
                                return result(
                                    "failed", "upstream_transport_error", "The account test connection failed."
                                )
                            code, message = _safe_error(
                                error,
                                access_token=access_token,
                                fallback_code="response_failed",
                                fallback_message="Upstream failed the account test.",
                            )
                            return result("failed", code, message)
    except ProxyResponseError as exc:
        # Only the trace proves an actual HTTP response; connector failures can
        # carry a synthetic HTTP status in the exception.
        if trace.http_status is None:
            return result("failed", "upstream_transport_error", "The account test connection failed.")
        code, message = _safe_error(
            OpenAIError.model_validate(exc.payload["error"]),
            access_token=access_token,
            fallback_code="upstream_http_error",
            fallback_message="Upstream rejected the account test.",
        )
        return result("failed", code, message)
    except UpstreamProxyRouteError:
        return result("failed", "upstream_proxy_unavailable", "The account's upstream proxy route is unavailable.")
    except (TimeoutError, StreamIdleTimeoutError):
        return result("incomplete", "probe_timeout", "Account test timed out before completion.")
    except StreamEventTooLargeError:
        return result("incomplete", "probe_stream_limit", "Account test exceeded its stream limit.")
    except (CodexTransportError, aiohttp.ClientError):
        return result("failed", "upstream_transport_error", "The account test connection failed.")
    return result("incomplete", "stream_incomplete", "Account test stream ended before completion.")
