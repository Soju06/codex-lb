from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import hashlib
import ipaddress
import json
import logging
import os
import re
import socket
import time
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import (
    Any,
    AsyncContextManager,
    AsyncIterator,
    Awaitable,
    Callable,
    Final,
    Mapping,
    Protocol,
    Sequence,
    TypeAlias,
    TypeVar,
    cast,
)
from urllib.parse import ParseResult, urlparse, urlunparse

import aiohttp
from aiohttp import hdrs
from aiohttp.client_ws import DEFAULT_WS_CLIENT_TIMEOUT, WebSocketDataQueue
from aiohttp.http_websocket import WS_KEY, WebSocketReader, WebSocketWriter
from multidict import CIMultiDict

from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    codex_transport_error_message,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.codex_version import get_codex_version_cache
from app.core.clients.http import acquire_http_client, lease_http_session
from app.core.config.settings import Settings, get_settings
from app.core.conversation_archive import archive_json, archive_text
from app.core.errors import (
    OpenAIErrorDetail,
    OpenAIErrorEnvelope,
    ResponseFailedEvent,
    openai_error,
    response_failed_event,
)
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.model_registry import get_model_registry
from app.core.openai.models import CompactResponsePayload, OpenAIError
from app.core.openai.parsing import (
    parse_compact_response_payload,
    parse_error_payload,
    parse_sse_event,
)
from app.core.openai.requests import (
    ResponsesCompactRequest,
    ResponsesRequest,
    validate_compact_input_wire_budget,
)
from app.core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    _is_server_error,
    get_circuit_breaker_for_account,
)
from app.core.resilience.network_recovery import (
    PROCESS_NETWORK_UNAVAILABLE_CODE,
    is_pre_dispatch_connection_failure,
    is_process_network_failure,
    is_proxy_endpoint_failure,
    process_network_error_code,
)
from app.core.types import JsonObject, JsonValue
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.usage.live_hub import publish_live_usage
from app.core.usage.live_snapshots import EVENT_MARKER, parse_rate_limit_event_text, parse_rate_limit_headers
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event, parse_sse_data_json

CODEX_INSTALLATION_ID_HEADER = "x-codex-installation-id"
CODEX_TURN_METADATA_HEADER = "x-codex-turn-metadata"
CODEX_RESPONSES_LITE_HEADER = "x-openai-internal-codex-responses-lite"
CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY = "ws_request_header_x_openai_internal_codex_responses_lite"

IGNORE_INBOUND_HEADERS = {
    "authorization",
    "chatgpt-account-id",
    "content-length",
    "host",
    "forwarded",
    "x-real-ip",
    CODEX_INSTALLATION_ID_HEADER,
    "true-client-ip",
}
INTERNAL_OPENAI_UPSTREAM_HEADERS = frozenset(
    {
        CODEX_RESPONSES_LITE_HEADER,
    }
)

_ERROR_TYPE_CODE_MAP = {
    "rate_limit_exceeded": "rate_limit_exceeded",
    "usage_not_included": "usage_not_included",
    "insufficient_quota": "insufficient_quota",
    "quota_exceeded": "quota_exceeded",
}

_SSE_EVENT_TYPE_ALIASES = {
    "response.text.delta": "response.output_text.delta",
    "response.audio.delta": "response.output_audio.delta",
    "response.audio_transcript.delta": "response.output_audio_transcript.delta",
}
_SSE_LINE_BOUNDARY_RE = re.compile(r"\r\n|\r|\n")
_RESPONSE_STREAM_TERMINAL_EVENT_TYPES = frozenset(
    {
        "response.completed",
        "response.failed",
        "response.incomplete",
    }
)


def _is_response_stream_terminal_event_type(event_type: str, *, enforce_openai_sdk_contract: bool) -> bool:
    if event_type in _RESPONSE_STREAM_TERMINAL_EVENT_TYPES:
        return True
    return event_type == "error" and not enforce_openai_sdk_contract


# 16 KiB reads: 1 KiB reads made a multi-MB SSE event cost thousands of
# iterations, and each iteration's separator rescan froze the event loop
# (see the scan cursor in _iter_sse_events).
_SSE_READ_CHUNK_SIZE = 16 * 1024
# Longest separator is 4 bytes; a separator can straddle a chunk boundary by
# at most 3 bytes, so rescans back up this far into already-scanned bytes.
_SSE_SEPARATOR_OVERLAP = 3
_IMAGE_INLINE_MAX_BYTES = 8 * 1024 * 1024
_IMAGE_INLINE_CHUNK_SIZE = 64 * 1024
_IMAGE_INLINE_TIMEOUT_SECONDS = 8.0
_WEBSOCKET_TRANSPORT_HEADROOM_BYTES: Final[int] = 2 * 1024 * 1024
_BLOCKED_LITERAL_HOSTS = {"localhost", "localhost.localdomain"}
_UPSTREAM_RESPONSE_CREATE_MAX_BYTES = get_settings().upstream_response_create_max_bytes
_UPSTREAM_RESPONSE_CREATE_WARN_BYTES = int(_UPSTREAM_RESPONSE_CREATE_MAX_BYTES * 0.8)
_RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE = (
    "[codex-lb omitted historical tool output ({bytes} bytes) to fit upstream websocket budget]"
)
_RESPONSE_CREATE_IMAGE_OMISSION_NOTICE = "[codex-lb omitted historical inline image to fit upstream websocket budget]"
_SLIMMABLE_TOOL_CALL_OUTPUT_ITEM_TYPES = frozenset(
    {"function_call_output", "custom_tool_call_output", "apply_patch_call_output"}
)
_UPSTREAM_TRACE_HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "chatgpt-account-id",
        "content-type",
        "request-id",
        "session-id",
        "session_id",
        "thread-id",
        "user-agent",
        "x-codex-conversation-id",
        "x-codex-session-id",
        "x-openai-client-arch",
        "x-openai-client-id",
        "x-openai-client-os",
        "x-openai-client-user-agent",
        "x-openai-client-version",
        "x-request-id",
    }
)
_NATIVE_CODEX_ORIGINATORS = frozenset(
    {
        "Codex Desktop",
        "codex_atlas",
        "codex_chatgpt_desktop",
        "codex_cli_rs",
        "codex_exec",
        "codex_sdk_ts",
        "codex_vscode",
    }
)
_NATIVE_CODEX_STREAM_HEADER_KEYS = frozenset(
    {
        "x-codex-turn-state",
        "x-codex-turn-metadata",
        "x-codex-beta-features",
    }
)
_HOP_BY_HOP_HEADER_NAMES = frozenset(
    {
        "accept",
        "connection",
        "content-type",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_AUTO_WEBSOCKET_HANDSHAKE_FALLBACK_STATUSES = frozenset({426})
_WEBSOCKET_RESPONSE_CREATE_EXCLUDED_FIELDS = frozenset({"background", "stream"})
_WEBSOCKET_HANDSHAKE_ERROR_HINTS = (
    ("account_deactivated", "account has been deactivated"),
    ("usage_not_included", "usage not included"),
    ("insufficient_quota", "insufficient quota"),
    ("quota_exceeded", "quota exceeded"),
    ("usage_limit_reached", "usage limit reached"),
    ("rate_limit_exceeded", "rate limit"),
)

logger = logging.getLogger(__name__)
_STREAM_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "stream_connect_timeout_override",
    default=None,
)
_STREAM_IDLE_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "stream_idle_timeout_override",
    default=None,
)
_STREAM_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "stream_total_timeout_override",
    default=None,
)
_COMPACT_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "compact_connect_timeout_override",
    default=None,
)
_COMPACT_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "compact_total_timeout_override",
    default=None,
)
_TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "transcribe_connect_timeout_override",
    default=None,
)
_TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "transcribe_total_timeout_override",
    default=None,
)

R = TypeVar("R")


@dataclass(slots=True)
class UpstreamProxyRouteTrace:
    mode: str | None = None
    pool_id: str | None = None
    endpoint_id: str | None = None
    fallback_used: bool | None = None

    def record(self, *, route: ResolvedUpstreamRoute, fallback_used: bool) -> None:
        self.mode = route.mode
        self.pool_id = route.pool_id
        self.endpoint_id = route.endpoint_id
        self.fallback_used = fallback_used

    def record_direct(self) -> None:
        self.mode = "direct"
        self.pool_id = None
        self.endpoint_id = None
        self.fallback_used = None


def _codex_route_transport_error_message(
    *,
    route: ResolvedUpstreamRoute | None,
    route_trace: UpstreamProxyRouteTrace | None,
    operation: str,
    exc: Exception,
) -> str:
    if route is None:
        return str(exc) or "Request to upstream failed"
    endpoint_id = route_trace.endpoint_id if route_trace is not None and route_trace.endpoint_id else route.endpoint_id
    return codex_transport_error_message(operation, endpoint_id, exc)


async def _call_with_service_circuit_breaker(
    request: Awaitable[R],
    *,
    settings: Settings | None = None,
    account_id: str | None = None,
) -> R:
    if not account_id:
        return await request
    effective_settings = settings or get_settings()
    circuit_breaker = get_circuit_breaker_for_account(account_id, effective_settings)
    if circuit_breaker is None:
        return await request
    return await circuit_breaker.call(request)


@asynccontextmanager
async def _service_circuit_breaker_context(
    cm: AsyncContextManager[aiohttp.ClientResponse],
    *,
    settings: Settings | None = None,
    account_id: str | None = None,
) -> AsyncIterator[aiohttp.ClientResponse]:
    """Wrap an async context manager with circuit breaker protection."""
    effective_settings = settings or get_settings()
    cb = get_circuit_breaker_for_account(account_id, effective_settings) if account_id else None
    is_probe = False
    if cb is not None:
        try:
            is_probe = await cb.pre_call_check()
        except BaseException:
            close = getattr(cm, "close", None)
            if callable(close):
                close()
            raise
    resp_ref: aiohttp.ClientResponse | None = None
    try:
        async with cm as resp:
            resp_ref = resp
            yield resp
        if cb is not None:
            if hasattr(resp, "status") and resp.status >= 500:
                await cb._record_failure(Exception(f"HTTP {resp.status}"))
            else:
                await cb._record_success()
    except CircuitBreakerOpenError:
        raise
    except Exception as e:
        if cb is not None:
            if (
                resp_ref is not None
                and hasattr(resp_ref, "status")
                and resp_ref.status < 500
                and not _is_server_error(e)
            ):
                await cb._record_success()
            else:
                await _record_account_circuit_breaker_failure(cb, e)
        raise
    finally:
        if is_probe and cb is not None:
            await cb.release_half_open_probe()


def _is_process_network_transport_error(exc: BaseException) -> bool:
    # A permanent lookup failure for the configured proxy hostname identifies
    # that endpoint, not a host-wide outage; transient proxy DNS remains neutral.
    return is_process_network_failure(
        exc,
        include_permanent_dns=not is_proxy_endpoint_failure(exc),
    ) or (isinstance(exc, CodexTransportError) and exc.error_code == PROCESS_NETWORK_UNAVAILABLE_CODE)


async def _record_account_circuit_breaker_failure(circuit_breaker: CircuitBreaker, exc: Exception) -> bool:
    """Record endpoint/account failures while keeping host-network loss neutral."""

    if _is_process_network_transport_error(exc):
        return False
    await circuit_breaker._record_failure(exc)
    return True


_HELD_HALF_OPEN_PROBE_FLAG = "_codex_lb_half_open_probe_held"
_HELD_HALF_OPEN_PROBE_BREAKER = "_codex_lb_half_open_probe_breaker"


def _bind_half_open_probe(
    websocket: aiohttp.ClientWebSocketResponse,
    circuit_breaker: "CircuitBreaker",
) -> None:
    setattr(websocket, _HELD_HALF_OPEN_PROBE_FLAG, True)
    setattr(websocket, _HELD_HALF_OPEN_PROBE_BREAKER, circuit_breaker)


async def _release_bound_half_open_probe(websocket: aiohttp.ClientWebSocketResponse | None) -> None:
    if websocket is None or not getattr(websocket, _HELD_HALF_OPEN_PROBE_FLAG, False):
        return
    circuit_breaker = cast("CircuitBreaker | None", getattr(websocket, _HELD_HALF_OPEN_PROBE_BREAKER, None))
    setattr(websocket, _HELD_HALF_OPEN_PROBE_FLAG, False)
    setattr(websocket, _HELD_HALF_OPEN_PROBE_BREAKER, None)
    if circuit_breaker is not None:
        await circuit_breaker.release_half_open_probe()


class StreamIdleTimeoutError(Exception):
    pass


class StreamEventTooLargeError(Exception):
    def __init__(self, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(f"SSE event exceeded {limit_bytes} bytes (received {size_bytes} bytes)")
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class ErrorResponseProtocol(Protocol):
    status: int
    reason: str | None

    async def json(self, *, content_type: str | None = None) -> JsonValue: ...

    async def text(self, *, encoding: str | None = None, errors: str = "strict") -> str: ...


ErrorResponse: TypeAlias = aiohttp.ClientResponse | ErrorResponseProtocol


class SSEContentProtocol(Protocol):
    def iter_chunked(self, size: int) -> "SSEChunkIteratorProtocol": ...


class SSEChunkIteratorProtocol(Protocol):
    def __aiter__(self) -> "SSEChunkIteratorProtocol": ...

    def __anext__(self) -> Awaitable[bytes]: ...


class SSEResponseProtocol(Protocol):
    content: SSEContentProtocol


SSEResponse: TypeAlias = aiohttp.ClientResponse | SSEResponseProtocol


class _CodexSSEContent:
    def __init__(self, response: Any) -> None:
        self._response = response

    def iter_chunked(self, size: int) -> "SSEChunkIteratorProtocol":
        del size
        return cast(SSEChunkIteratorProtocol, self._response.content.iter_chunked(1024))


class _CodexSSEResponse:
    reason: str | None = None

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status = _codex_response_status(response)
        self.content = _CodexSSEContent(response)

    async def json(self, *, content_type: str | None = None) -> JsonValue:
        del content_type
        return cast(JsonValue, await _codex_response_json(self._response))

    async def text(self, *, encoding: str | None = None, errors: str = "strict") -> str:
        del encoding, errors
        return await _codex_response_text(self._response)


class ProxyResponseError(Exception):
    status_code: int
    payload: OpenAIErrorEnvelope

    def __init__(
        self,
        status_code: int,
        payload: OpenAIErrorEnvelope,
        *,
        failure_phase: str | None = None,
        retryable_same_contract: bool = False,
        failure_detail: str | None = None,
        failure_exception_type: str | None = None,
        upstream_status_code: int | None = None,
        upstream_error_code: str | None = None,
        failed_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(f"Proxy response error ({status_code})")
        self.status_code = status_code
        self.payload = payload
        self.failure_phase = failure_phase
        self.retryable_same_contract = retryable_same_contract
        self.failure_detail = failure_detail
        self.failure_exception_type = failure_exception_type
        self.upstream_status_code = upstream_status_code
        self.upstream_error_code = upstream_error_code
        self.failed_session = failed_session


def _process_network_failure_error(
    message: str,
    exc: Exception,
    *,
    retryable_same_contract: bool,
    failed_session: aiohttp.ClientSession | None,
) -> ProxyResponseError:
    """Preserve replay provenance and failed shared generation independently."""

    # Dispatch may make the request unsafe to replay, but it does not make the
    # concrete failed generation safe for subsequent requests to keep using.
    return ProxyResponseError(
        502,
        openai_error(PROCESS_NETWORK_UNAVAILABLE_CODE, message),
        failure_phase="connect" if retryable_same_contract else "upstream",
        retryable_same_contract=retryable_same_contract,
        failure_detail="process_network_connect_error" if retryable_same_contract else "transport_error",
        failure_exception_type=type(exc).__name__,
        failed_session=failed_session,
    )


def _failed_shared_session_for_process_network_error(
    error_code: str,
    session: aiohttp.ClientSession,
) -> aiohttp.ClientSession | None:
    # Replay safety answers whether this request may run again; it must not
    # decide whether later callers inherit a concrete failed generation.
    return session if error_code == PROCESS_NETWORK_UNAVAILABLE_CODE else None


@dataclass(frozen=True)
class CodexControlResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


def _should_drop_inbound_header(name: str) -> bool:
    normalized = name.lower()
    if normalized in IGNORE_INBOUND_HEADERS:
        return True
    if normalized in INTERNAL_OPENAI_UPSTREAM_HEADERS:
        return True
    if normalized.startswith("x-forwarded-"):
        return True
    if normalized.startswith("cf-"):
        return True
    return False


def filter_inbound_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in headers.items() if not _should_drop_inbound_header(key)}


def _rewrite_turn_metadata_installation_id(value: JsonValue, codex_installation_id: str | None) -> JsonValue:
    if not codex_installation_id or not isinstance(value, str):
        return value
    try:
        metadata = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(metadata, dict) or "installation_id" not in metadata:
        return value
    metadata["installation_id"] = codex_installation_id
    return json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))


def apply_codex_installation_metadata(payload: dict[str, JsonValue], codex_installation_id: str | None) -> None:
    raw_metadata = payload.get("client_metadata")
    client_metadata: dict[str, JsonValue] = {}
    if is_json_mapping(raw_metadata):
        for key, value in raw_metadata.items():
            if not isinstance(key, str) or key.lower() == CODEX_INSTALLATION_ID_HEADER:
                continue
            client_metadata[key] = (
                _rewrite_turn_metadata_installation_id(value, codex_installation_id)
                if key.lower() == CODEX_TURN_METADATA_HEADER
                else value
            )
    if codex_installation_id:
        client_metadata[CODEX_INSTALLATION_ID_HEADER] = codex_installation_id
    if client_metadata:
        payload["client_metadata"] = client_metadata
    else:
        payload.pop("client_metadata", None)


def apply_codex_installation_headers(
    headers: Mapping[str, str],
    codex_installation_id: str | None,
) -> dict[str, str]:
    """Keep canonical turn metadata consistent with the selected account."""
    updated = dict(headers)
    if not codex_installation_id:
        return updated
    has_installation_id = False
    for key, value in list(updated.items()):
        lowered = key.lower()
        if lowered == CODEX_INSTALLATION_ID_HEADER:
            updated[key] = codex_installation_id
            has_installation_id = True
        elif lowered == CODEX_TURN_METADATA_HEADER:
            rewritten = _rewrite_turn_metadata_installation_id(value, codex_installation_id)
            if isinstance(rewritten, str):
                updated[key] = rewritten
    if not has_installation_id:
        updated[CODEX_INSTALLATION_ID_HEADER] = codex_installation_id
    return updated


_NATIVE_CODEX_USER_AGENT_PREFIXES: tuple[str, ...] = (
    "codex_cli_rs",
    "codex-tui",
    "codex_exec",
    "codex_sdk_ts",
    "codex_vscode",
    "codex desktop",
    "codex ",
)
_SDK_FINGERPRINT_HEADER_KEYS: frozenset[str] = frozenset(
    {
        "x-openai-client-version",
        "x-openai-client-os",
        "x-openai-client-arch",
        "x-openai-client-id",
        "x-openai-client-user-agent",
    }
)
# OpenAI SDKs (the Stainless-generated clients) attach an ``x-stainless-*``
# header family (os, arch, runtime, runtime-version, package-version, ...) with
# a variable suffix. The API layer treats any ``x-stainless-*`` header as an
# OpenAI SDK signal, so they must be stripped by prefix on a normalized request
# or upstream can still distinguish SDK traffic from the Codex CLI and apply the
# downgrade this change is meant to avoid.
_SDK_FINGERPRINT_HEADER_PREFIXES: tuple[str, ...] = ("x-stainless-",)
_CODEX_CLI_ORIGINATOR = "codex_cli_rs"
_CHATGPT_ACCOUNT_ID_HEADER = "ChatGPT-Account-Id"
# Fixed Codex client fingerprint (issue #1340 / PRINCIPLES.md P2). These
# values impersonate a plausible first-party Codex CLI install and are
# maintained in lockstep with ``model_registry_client_version`` bumps; they
# are not deployment tunables.
_FINGERPRINT_OS = "Mac OS 26.5.0"
_FINGERPRINT_ARCH = "arm64"
_FINGERPRINT_TERMINAL = "iTerm.app/3.6.10"


def build_codex_user_agent(version: str) -> str:
    """Build a Codex CLI ``User-Agent`` matching ``get_codex_user_agent()`` from
    ``openai/codex`` (``codex-rs/login/src/auth/default_client.rs``):
    ``codex_cli_rs/<version> (<os>; <arch>) <terminal>``.

    OS/arch/terminal are fixed fingerprint constants; the version is the live
    Codex client version resolved by the caller.
    """
    return f"{_CODEX_CLI_ORIGINATOR}/{version} ({_FINGERPRINT_OS}; {_FINGERPRINT_ARCH}) {_FINGERPRINT_TERMINAL}"


def _is_native_codex_user_agent(user_agent: str | None) -> bool:
    if not user_agent:
        return False
    lowered = user_agent.strip().lower()
    return any(lowered.startswith(prefix) for prefix in _NATIVE_CODEX_USER_AGENT_PREFIXES)


def _is_native_codex_request(headers: Mapping[str, str]) -> bool:
    """A request is native when its identity headers mark it as a first-party
    Codex client: either a native Codex ``User-Agent`` prefix, or an
    ``originator`` header whose value is in the native Codex originator set.

    Transport/continuity headers (``x-codex-turn-state`` and friends) are
    deliberately NOT treated as native signals: an HTTP SDK client replays the
    ``x-codex-turn-state`` token the upstream returns for continuity, so keying
    the exemption on those headers would let a non-native SDK follow-up skip
    normalization and reach upstream with its downgraded fingerprint intact.
    """
    user_agent = None
    originator = None
    for key, value in headers.items():
        lowered = key.lower()
        if lowered == "user-agent":
            user_agent = value
        elif lowered == "originator":
            originator = value
    if _is_native_codex_user_agent(user_agent):
        return True
    return _is_native_codex_originator(originator)


def _normalize_non_native_upstream_fingerprint(headers: dict[str, str]) -> None:
    """Rewrite a non-native request's outbound fingerprint to the Codex CLI
    persona in place: set ``User-Agent`` to a ``codex_cli_rs`` string, strip
    SDK-only ``x-openai-client-*`` and ``x-stainless-*`` headers, and replace
    any inbound ``originator`` or ``version`` with the canonical Codex values."""
    version = get_codex_version_cache().cached_version_or_default()
    codex_user_agent = build_codex_user_agent(version)
    for key in list(headers.keys()):
        lowered = key.lower()
        if (
            lowered == "user-agent"
            or lowered in _SDK_FINGERPRINT_HEADER_KEYS
            or lowered.startswith(_SDK_FINGERPRINT_HEADER_PREFIXES)
            or lowered == "originator"
            or lowered == "version"
        ):
            del headers[key]
    headers["User-Agent"] = codex_user_agent
    headers["originator"] = _CODEX_CLI_ORIGINATOR
    headers["version"] = version


def _build_upstream_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    accept: str = "text/event-stream",
) -> dict[str, str]:
    headers = filter_inbound_headers(inbound)
    native = _is_native_codex_request(headers)
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    if not native:
        _normalize_non_native_upstream_fingerprint(headers)
    headers["Authorization"] = f"Bearer {access_token}"
    headers["Accept"] = accept
    headers["Content-Type"] = "application/json"
    if account_id:
        if native:
            headers["chatgpt-account-id"] = account_id
        else:
            headers[_CHATGPT_ACCOUNT_ID_HEADER] = account_id
    return headers


_TRANSCRIBE_FORWARD_HEADER_PREFIXES = ("x-openai-", "x-codex-")


def _build_upstream_transcribe_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    # Minimal header set matching Codex CLI ``/transcribe`` fingerprint.
    # Omit Accept, x-request-id, and bulk-forwarded inbound headers to
    # avoid upstream WAF rejection.
    headers: dict[str, str] = {}
    headers["Authorization"] = f"Bearer {access_token}"
    if account_id:
        headers["chatgpt-account-id"] = account_id
    for key, value in inbound.items():
        lower = key.lower()
        if lower == "user-agent":
            headers[key] = value
        elif lower.startswith(_TRANSCRIBE_FORWARD_HEADER_PREFIXES):
            headers[key] = value
    return headers


def _build_upstream_websocket_headers(
    inbound: Mapping[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    connected_header_tokens: set[str] = set()
    for key, value in inbound.items():
        if key.lower() != "connection":
            continue
        connected_header_tokens.update(
            token.strip().lower() for token in value.split(",") if isinstance(value, str) and token.strip()
        )
    blocked_header_names = _HOP_BY_HOP_HEADER_NAMES | connected_header_tokens
    filtered = filter_inbound_headers(inbound)
    headers = {key: value for key, value in filtered.items() if key.lower() not in blocked_header_names}
    native = _is_native_codex_request(headers)
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    # Normalize a non-native client's fingerprint regardless of transport. The
    # ``auto`` transport routes a turn-state continuity follow-up onto the
    # websocket path even for an HTTP SDK client, so this builder must apply the
    # same codex_cli_rs persona rewrite as ``_build_upstream_headers``; otherwise
    # the SDK fingerprint reaches upstream unchanged and the priority-downgrade
    # mitigation is bypassed for exactly the continuity-token scenario.
    if not native:
        _normalize_non_native_upstream_fingerprint(headers)
    headers["Authorization"] = f"Bearer {access_token}"
    if account_id:
        if native:
            headers["chatgpt-account-id"] = account_id
        else:
            headers[_CHATGPT_ACCOUNT_ID_HEADER] = account_id
    return headers


def _interesting_upstream_header_keys(headers: Mapping[str, str]) -> list[str]:
    return sorted({key.lower() for key in headers if key.lower() in _UPSTREAM_TRACE_HEADER_ALLOWLIST})


def _summarize_upstream_target(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _summarize_input_value(value: JsonValue | None) -> str:
    if value is None:
        return "0"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        if not value:
            return "0"
        type_counts: dict[str, int] = {}
        for item in value:
            type_name = type(item).__name__
            type_counts[type_name] = type_counts.get(type_name, 0) + 1
        summary = ",".join(f"{key}={type_counts[key]}" for key in sorted(type_counts))
        return f"{len(value)}({summary})"
    return type(value).__name__


def _summarize_json_payload(payload: Mapping[str, JsonValue]) -> str:
    keys = sorted(payload.keys())
    model = payload.get("model")
    stream = payload.get("stream")
    input_summary = _summarize_input_value(payload.get("input"))
    return f"model={model} stream={stream} input={input_summary} keys={keys}"


def _summarize_transcription_payload(
    *,
    filename: str,
    content_type: str | None,
    prompt: str | None,
    audio_bytes: bytes,
) -> dict[str, JsonValue]:
    return {
        "filename": filename,
        "content_type": content_type,
        "prompt_present": prompt is not None,
        "audio_bytes": len(audio_bytes),
    }


def _error_details_from_envelope(payload: OpenAIErrorEnvelope) -> tuple[str | None, str | None]:
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


def _error_details_from_failed_event(payload: ResponseFailedEvent) -> tuple[str | None, str | None]:
    response = payload.get("response")
    if not isinstance(response, dict):
        return None, None
    error = response.get("error")
    if not isinstance(error, dict):
        return None, None
    code = error.get("code")
    message = error.get("message")
    return code if isinstance(code, str) else None, message if isinstance(message, str) else None


def _extract_json_object_from_text(text: str) -> JsonValue | None:
    if not text:
        return None
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        return value
    return None


def _infer_websocket_handshake_error_code(status: int | None, message: str) -> str:
    lowered = message.lower()
    for code, hint in _WEBSOCKET_HANDSHAKE_ERROR_HINTS:
        if hint in lowered:
            return code
    if status == 401:
        return "invalid_api_key"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limit_exceeded"
    return "upstream_error"


def _error_payload_from_websocket_handshake_error(exc: aiohttp.WSServerHandshakeError) -> OpenAIErrorEnvelope:
    message = exc.message or str(exc)
    extracted = _extract_json_object_from_text(message)
    if extracted is not None:
        error = parse_error_payload(extracted)
        if error is not None:
            return {"error": _openai_error_detail(error)}

    code = _infer_websocket_handshake_error_code(exc.status, message)
    if code == "invalid_api_key":
        return openai_error(code, message, error_type="authentication_error")
    if code == "not_found":
        return openai_error(code, message, error_type="invalid_request_error")
    if code == "rate_limit_exceeded":
        return openai_error(code, message, error_type="rate_limit_error")
    return openai_error(code, message)


def _account_id_for_upstream_log(headers: Mapping[str, str]) -> str | None:
    """Read the upstream account id from request headers case-insensitively.

    A normalized non-native request carries the id under PascalCase
    ``ChatGPT-Account-Id`` while native requests use lowercase
    ``chatgpt-account-id``; a case-sensitive lookup would log ``account_id=None``
    for normalized SDK traffic and drop the per-account diagnostics this feature
    exists to provide.
    """
    for key, value in headers.items():
        if key.lower() == "chatgpt-account-id":
            return value
    return None


def _maybe_log_upstream_request_start(
    *,
    kind: str,
    url: str,
    headers: Mapping[str, str],
    method: str,
    payload_summary: str,
    payload_json: str | None = None,
) -> None:
    trace_channels = get_settings().trace_channels
    if "upstream_summary" not in trace_channels and "upstream_payload" not in trace_channels:
        return

    request_id = get_request_id()
    target = _summarize_upstream_target(url)
    account_id = _account_id_for_upstream_log(headers)
    header_keys = _interesting_upstream_header_keys(headers)

    if "upstream_summary" in trace_channels:
        logger.info(
            "upstream_request_start request_id=%s kind=%s method=%s target=%s account_id=%s headers=%s payload=%s",
            request_id,
            kind,
            method,
            target,
            account_id,
            header_keys,
            payload_summary,
        )
    if "upstream_payload" in trace_channels and payload_json is not None:
        logger.info(
            "upstream_request_payload request_id=%s kind=%s target=%s payload=%s",
            request_id,
            kind,
            target,
            payload_json,
        )


def _maybe_log_upstream_request_complete(
    *,
    kind: str,
    url: str,
    headers: Mapping[str, str],
    method: str,
    started_at: float,
    status_code: int | None,
    error_code: str | None,
    error_message: str | None,
    failure_phase: str | None = None,
    payload_object: str | None = None,
    failure_detail: str | None = None,
    failure_exception_type: str | None = None,
    retryable_same_contract: bool | None = None,
) -> None:
    if "upstream_summary" not in get_settings().trace_channels:
        return

    level = logging.INFO
    if status_code is not None and status_code >= 500:
        level = logging.ERROR
    elif (status_code is not None and status_code >= 400) or error_code is not None:
        level = logging.WARNING

    logger.log(
        level,
        (
            "upstream_request_complete request_id=%s kind=%s method=%s target=%s "
            "account_id=%s status=%s duration_ms=%s error_code=%s error_message=%s "
            "failure_phase=%s payload_object=%s failure_detail=%s failure_exception_type=%s "
            "retryable_same_contract=%s"
        ),
        get_request_id(),
        kind,
        method,
        _summarize_upstream_target(url),
        _account_id_for_upstream_log(headers),
        status_code,
        int((time.monotonic() - started_at) * 1000),
        error_code,
        error_message,
        failure_phase,
        payload_object,
        failure_detail,
        failure_exception_type,
        retryable_same_contract,
    )


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


def _effective_stream_timeout(configured_timeout_seconds: float, timeout_kind: str) -> float:
    if timeout_kind == "connect":
        override = _STREAM_CONNECT_TIMEOUT_OVERRIDE.get()
    elif timeout_kind == "idle":
        override = _STREAM_IDLE_TIMEOUT_OVERRIDE.get()
    else:
        override = _STREAM_TOTAL_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_compact_connect_timeout(configured_timeout_seconds: float) -> float:
    override = _COMPACT_CONNECT_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_compact_total_timeout(configured_timeout_seconds: float | None) -> float | None:
    override = _COMPACT_TOTAL_TIMEOUT_OVERRIDE.get()
    if configured_timeout_seconds is None:
        return None if override is None else max(0.001, override)
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_transcribe_connect_timeout(configured_timeout_seconds: float) -> float:
    override = _TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _effective_transcribe_total_timeout(configured_timeout_seconds: float) -> float:
    override = _TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


def _remaining_total_timeout(timeout_seconds: float | None, started_at: float, now: float) -> float | None:
    if timeout_seconds is None:
        return None
    return max(0.001, timeout_seconds - max(0.0, now - started_at))


def _find_sse_separator(buffer: bytes | bytearray, start: int = 0) -> tuple[int, int] | None:
    separators = (b"\r\n\r\n", b"\n\n", b"\r\r")
    positions = [(buffer.find(separator, start), len(separator)) for separator in separators]
    valid_positions = [position for position in positions if position[0] >= 0]
    if not valid_positions:
        return None
    return min(valid_positions, key=lambda item: item[0])


def _pop_sse_event(buffer: bytearray) -> bytes | None:
    separator = _find_sse_separator(buffer)
    if separator is None:
        return None
    index, separator_len = separator
    event_end = index + separator_len
    event = bytes(buffer[:event_end])
    del buffer[:event_end]
    return event


async def _iter_sse_events(
    resp: SSEResponse,
    idle_timeout_seconds: float,
    max_event_bytes: int,
) -> AsyncIterator[str]:
    async def _next_chunk() -> bytes:
        return await iterator.__anext__()

    async def _cancel_pending_chunk(task: asyncio.Task[bytes]) -> None:
        if task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    buffer = bytearray()
    scanned = 0
    chunk_iterator = resp.content.iter_chunked(_SSE_READ_CHUNK_SIZE)
    iterator = chunk_iterator.__aiter__()

    while True:
        next_chunk = asyncio.create_task(_next_chunk())
        try:
            done, _ = await asyncio.wait({next_chunk}, timeout=idle_timeout_seconds)
            if not done:
                await _cancel_pending_chunk(next_chunk)
                raise StreamIdleTimeoutError()
            chunk = await next_chunk
        except StopAsyncIteration:
            break
        except asyncio.CancelledError:
            await _cancel_pending_chunk(next_chunk)
            raise

        if not chunk:
            continue

        buffer.extend(chunk)
        while True:
            # `scanned` marks the prefix already known to hold no separator,
            # so each new chunk only scans the new bytes (plus the straddle
            # overlap). Without the cursor, a large event re-scanned the
            # entire accumulated buffer on every read — O(n^2) byte scanning
            # that blocked the event loop for every in-flight stream.
            separator = _find_sse_separator(buffer, max(0, scanned - _SSE_SEPARATOR_OVERLAP))
            if separator is None:
                scanned = len(buffer)
                if len(buffer) > max_event_bytes:
                    raise StreamEventTooLargeError(len(buffer), max_event_bytes)
                break
            index, separator_len = separator
            event_end = index + separator_len
            raw_event = bytes(buffer[:event_end])
            del buffer[:event_end]
            scanned = 0

            if len(raw_event) > max_event_bytes:
                raise StreamEventTooLargeError(len(raw_event), max_event_bytes)

            if raw_event.strip():
                yield raw_event.decode("utf-8", errors="replace")

    if buffer:
        if len(buffer) > max_event_bytes:
            raise StreamEventTooLargeError(len(buffer), max_event_bytes)
        yield bytes(buffer).decode("utf-8", errors="replace")


async def _error_response_body(resp: ErrorResponse) -> tuple[object | None, str | None]:
    try:
        return await resp.json(content_type=None), None
    except Exception:
        return None, await resp.text()


def _error_archive_payload(data: object | None, text: str | None) -> object:
    if data is not None:
        return data
    return {"text": text or ""}


def _error_event_from_response_body(
    resp: ErrorResponse,
    *,
    data: object | None,
    text: str | None,
) -> ResponseFailedEvent:
    fallback_message = f"Upstream error: HTTP {resp.status}"
    if resp.reason:
        fallback_message += f" {resp.reason}"
    if data is None:
        message = (text or "").strip() or fallback_message
        return response_failed_event("upstream_error", message, response_id=get_request_id())

    if is_json_mapping(data):
        payload_data = cast(dict[str, JsonValue], data)
        error = parse_error_payload(payload_data)
        if error:
            payload = error.model_dump(exclude_none=True)
            event = response_failed_event(
                _normalize_error_code(payload.get("code"), payload.get("type")),
                payload.get("message", fallback_message),
                error_type=payload.get("type") or "server_error",
                response_id=get_request_id(),
                error_param=payload.get("param"),
            )
            _copy_quota_error_metadata(event["response"]["error"], payload)
            return event
        message = _extract_upstream_message(payload_data)
        if message:
            return response_failed_event("upstream_error", message, response_id=get_request_id())
    return response_failed_event("upstream_error", fallback_message, response_id=get_request_id())


async def _error_event_from_response(resp: ErrorResponse) -> ResponseFailedEvent:
    data, text = await _error_response_body(resp)
    return _error_event_from_response_body(resp, data=data, text=text)


def _error_payload_from_response_body(
    resp: ErrorResponse,
    *,
    data: object | None,
    text: str | None,
) -> OpenAIErrorEnvelope:
    fallback_message = f"Upstream error: HTTP {resp.status}"
    if resp.reason:
        fallback_message += f" {resp.reason}"
    if data is None:
        message = (text or "").strip() or fallback_message
        return openai_error("upstream_error", message)

    if is_json_mapping(data):
        payload_data = cast(dict[str, JsonValue], data)
        error = parse_error_payload(payload_data)
        if error:
            return {"error": _openai_error_detail(error)}
        message = _extract_upstream_message(payload_data)
        if message:
            return openai_error("upstream_error", message)
    return openai_error("upstream_error", fallback_message)


async def _error_payload_from_response(resp: ErrorResponse) -> OpenAIErrorEnvelope:
    data, text = await _error_response_body(resp)
    return _error_payload_from_response_body(resp, data=data, text=text)


def _openai_error_detail(error: OpenAIError) -> OpenAIErrorDetail:
    detail: OpenAIErrorDetail = {}
    if error.message is not None:
        detail["message"] = error.message
    if error.type is not None:
        detail["type"] = error.type
    if error.code is not None:
        detail["code"] = error.code
    if error.param is not None:
        detail["param"] = error.param
    if error.plan_type is not None:
        detail["plan_type"] = error.plan_type
    if error.resets_at is not None:
        detail["resets_at"] = error.resets_at
    if error.resets_in_seconds is not None:
        detail["resets_in_seconds"] = error.resets_in_seconds
    return detail


def _copy_quota_error_metadata(target: OpenAIErrorDetail, source: Mapping[str, Any]) -> None:
    plan_type = source.get("plan_type")
    if isinstance(plan_type, str):
        target["plan_type"] = plan_type
    resets_at = source.get("resets_at")
    if isinstance(resets_at, int | float) and not isinstance(resets_at, bool):
        target["resets_at"] = resets_at
    resets_in_seconds = source.get("resets_in_seconds")
    if isinstance(resets_in_seconds, int | float) and not isinstance(resets_in_seconds, bool):
        target["resets_in_seconds"] = resets_in_seconds


def _extract_upstream_message(data: Mapping[str, JsonValue]) -> str | None:
    for key in ("message", "detail", "error"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


async def _error_payload_from_raw_body(resp: ErrorResponse, body: bytes) -> OpenAIErrorEnvelope:
    try:
        data: object | None = json.loads(body)
        text = None
    except Exception:
        data = None
        text = body.decode("utf-8", errors="replace")
    return _error_payload_from_response_body(resp, data=data, text=text)


def _normalize_sse_data_line(line: str) -> str:
    if not line.startswith("data:"):
        return line
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return line
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return line
    if not isinstance(payload, dict):
        return line
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type in _SSE_EVENT_TYPE_ALIASES:
        payload["type"] = _SSE_EVENT_TYPE_ALIASES[event_type]
        return f"data: {json.dumps(payload, ensure_ascii=True, separators=(',', ':'))}"
    return line


def _normalize_sse_event_block(event_block: str) -> str:
    if not event_block:
        return event_block

    if '"type":' not in event_block:
        return event_block

    if event_block.endswith("\r\n\r\n"):
        line_separator = "\r\n"
        terminator = "\r\n\r\n"
        body = event_block[: -len(terminator)]
    elif event_block.endswith("\n\n"):
        line_separator = "\n"
        terminator = "\n\n"
        body = event_block[: -len(terminator)]
    elif event_block.endswith("\r\r"):
        line_separator = "\r"
        terminator = "\r\r"
        body = event_block[: -len(terminator)]
    else:
        line_separator = "\r\n" if "\r\n" in event_block else "\n"
        terminator = ""
        body = event_block

    lines = _SSE_LINE_BOUNDARY_RE.split(body)
    if not lines:
        return event_block

    normalized_lines: list[str] = []
    changed = False
    for line in lines:
        normalized_line = _normalize_sse_data_line(line)
        if normalized_line != line:
            changed = True
        normalized_lines.append(normalized_line)
    if not changed:
        return event_block

    normalized = line_separator.join(normalized_lines)
    if terminator:
        return normalized + terminator
    return normalized


def _normalize_stream_event_payload(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type in _SSE_EVENT_TYPE_ALIASES:
        normalized = dict(payload)
        normalized["type"] = _SSE_EVENT_TYPE_ALIASES[event_type]
        return normalized
    error = parse_error_payload(payload)
    if error is not None:
        detail = error.model_dump(exclude_none=True)
        event = response_failed_event(
            _normalize_error_code(detail.get("code"), detail.get("type")),
            detail.get("message", "Upstream websocket error"),
            error_type=detail.get("type") or "server_error",
            response_id=get_request_id(),
            error_param=detail.get("param"),
        )
        _copy_quota_error_metadata(event["response"]["error"], detail)
        return cast(dict[str, JsonValue], event)
    if event_type == "error":
        message = _extract_upstream_message(payload) or "Upstream websocket error"
        code = payload.get("code")
        error_type = payload.get("error_type") or payload.get("type")
        normalized_code = _normalize_error_code(
            code if isinstance(code, str) else None,
            error_type if isinstance(error_type, str) else None,
        )
        if not isinstance(code, str) and normalized_code == "error":
            normalized_code = "upstream_error"
        return cast(
            dict[str, JsonValue],
            response_failed_event(
                normalized_code,
                message,
                error_type=error_type if isinstance(error_type, str) and error_type != "error" else "server_error",
                response_id=get_request_id(),
            ),
        )
    return payload


def _normalize_stream_payload_for_http_block(
    event_block: str,
    *,
    enforce_openai_sdk_contract: bool = True,
) -> tuple[str, str | None]:
    if not enforce_openai_sdk_contract:
        payload = parse_sse_data_json(event_block)
        if payload is None:
            return event_block, None
        event_type = payload.get("type")
        return event_block, event_type if isinstance(event_type, str) else None

    payload = parse_sse_data_json(event_block)
    if payload is None:
        return event_block, None
    normalized = _normalize_stream_event_payload(payload)
    if normalized is payload:
        event_type = normalized.get("type")
        return event_block, event_type if isinstance(event_type, str) else None
    normalized_type = normalized.get("type")
    event_type = normalized_type if isinstance(normalized_type, str) else None
    return format_sse_event(normalized), event_type


def _to_websocket_upstream_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        scheme = parsed.scheme
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def _configured_stream_transport(
    *,
    transport: str,
    transport_override: str | None = None,
) -> str:
    return transport_override if transport_override is not None else transport


def _has_native_codex_transport_headers(headers: Mapping[str, str]) -> bool:
    normalized = {key.lower(): value for key, value in headers.items()}
    originator = normalized.get("originator")
    if _is_native_codex_originator(originator):
        return True
    return any(key in normalized for key in _NATIVE_CODEX_STREAM_HEADER_KEYS)


def _is_native_codex_originator(originator: str | None) -> bool:
    if originator is None:
        return False
    stripped = originator.strip()
    if not stripped:
        return False
    return stripped in _NATIVE_CODEX_ORIGINATORS


def _payload_uses_image_generation_tool(payload: Mapping[str, JsonValue]) -> bool:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return False
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tool_type = tool.get("type")
        if tool_type == "image_generation":
            return True
    return False


def _payload_uses_responses_lite(payload: Mapping[str, JsonValue]) -> bool:
    input_value = payload.get("input")
    if not isinstance(input_value, list):
        return False
    return any(is_json_mapping(item) and item.get("type") == "additional_tools" for item in input_value)


def _client_metadata_uses_responses_lite(client_metadata: Mapping[str, JsonValue]) -> bool:
    return any(
        key.lower() == CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY
        and isinstance(value, str)
        and value.strip().lower() == "true"
        for key, value in client_metadata.items()
    )


def _payload_has_responses_lite_websocket_marker(payload: Mapping[str, JsonValue]) -> bool:
    raw_metadata = payload.get("client_metadata")
    return is_json_mapping(raw_metadata) and _client_metadata_uses_responses_lite(raw_metadata)


def _finalize_responses_lite_reasoning_context(
    payload: dict[str, JsonValue],
    *,
    responses_lite: bool,
) -> None:
    if not responses_lite:
        return
    raw_reasoning = payload.get("reasoning")
    if raw_reasoning is not None and not is_json_mapping(raw_reasoning):
        return
    reasoning = dict(raw_reasoning) if is_json_mapping(raw_reasoning) else {}
    reasoning["context"] = "all_turns"
    payload["reasoning"] = reasoning


def _normalize_responses_lite_websocket_client_metadata(
    payload: Mapping[str, JsonValue],
    client_metadata: Mapping[str, JsonValue],
    *,
    preserve_existing: bool = False,
) -> dict[str, JsonValue]:
    normalized = dict(client_metadata)
    existing_marker = _client_metadata_uses_responses_lite(normalized)
    for key in tuple(normalized):
        if key.lower() == CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY:
            del normalized[key]
    if _payload_uses_responses_lite(payload) or (preserve_existing and existing_marker):
        normalized[CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY] = "true"
    return normalized


def _strip_responses_lite_websocket_client_metadata(
    payload: dict[str, JsonValue],
) -> None:
    raw_metadata = payload.get("client_metadata")
    client_metadata = dict(raw_metadata) if is_json_mapping(raw_metadata) else {}
    for key in tuple(client_metadata):
        if key.lower() == CODEX_RESPONSES_LITE_WEBSOCKET_METADATA_KEY:
            del client_metadata[key]
    if client_metadata:
        payload["client_metadata"] = client_metadata
    else:
        payload.pop("client_metadata", None)


def _set_responses_lite_websocket_client_metadata(
    payload: dict[str, JsonValue],
) -> None:
    raw_metadata = payload.get("client_metadata")
    client_metadata = dict(raw_metadata) if is_json_mapping(raw_metadata) else {}
    normalized = _normalize_responses_lite_websocket_client_metadata(
        payload,
        client_metadata,
    )
    if normalized:
        payload["client_metadata"] = normalized
    else:
        payload.pop("client_metadata", None)


def _apply_responses_lite_http_header(
    headers: dict[str, str],
    payload: Mapping[str, JsonValue],
) -> None:
    if _payload_uses_responses_lite(payload):
        headers[CODEX_RESPONSES_LITE_HEADER] = "true"


def _ws_transport_payload_budget_bytes(settings: Settings | object) -> int:
    # Subtract 2 MiB headroom for control frames + envelope. ``getattr`` fallback
    # keeps unit tests that pass narrowed ``SimpleNamespace`` settings working
    # without forcing every fake to redeclare ``max_sse_event_bytes``.
    max_sse_event_bytes = getattr(settings, "max_sse_event_bytes", 16 * 1024 * 1024)
    return max(1 * 1024 * 1024, max_sse_event_bytes - _WEBSOCKET_TRANSPORT_HEADROOM_BYTES)


def _resolve_stream_transport(
    *,
    settings: Settings | object,
    transport: str,
    transport_override: str | None,
    model: str | None,
    headers: Mapping[str, str],
    has_image_generation_tool: bool = False,
    payload_size_estimate_bytes: int | None = None,
) -> str:
    configured = _configured_stream_transport(transport=transport, transport_override=transport_override)
    if configured == "websocket":
        return "websocket"
    if configured == "http":
        return "http"
    if payload_size_estimate_bytes is not None and payload_size_estimate_bytes > _ws_transport_payload_budget_bytes(
        settings
    ):
        return "http"
    if has_image_generation_tool:
        return "http"
    if _has_native_codex_transport_headers(headers):
        return "websocket"

    registry = get_model_registry()
    prefers_websockets = getattr(registry, "prefers_websockets", None)
    if callable(prefers_websockets):
        if prefers_websockets(model):
            return "websocket"
        return "http"

    snapshot = registry.get_snapshot()
    if snapshot is None or not isinstance(model, str):
        return "http"
    upstream_model = snapshot.models.get(model)
    if upstream_model and upstream_model.prefer_websockets:
        return "websocket"
    return "http"


def _should_fallback_to_http_after_websocket_handshake_error(
    transport_mode: str,
    exc: aiohttp.WSServerHandshakeError,
) -> bool:
    return _should_fallback_to_http_after_websocket_status(transport_mode, exc.status)


def _should_fallback_to_http_after_websocket_status(transport_mode: str, status: int | None) -> bool:
    return transport_mode == "auto" and status in _AUTO_WEBSOCKET_HANDSHAKE_FALLBACK_STATUSES


async def _open_upstream_websocket(
    *,
    session: aiohttp.ClientSession,
    url: str,
    headers: Mapping[str, str],
    connect_timeout_seconds: float,
    max_msg_size: int,
    account_id: str | None = None,
    hold_half_open_probe: bool = False,
) -> tuple[AsyncContextManager[aiohttp.ClientWebSocketResponse], aiohttp.ClientWebSocketResponse]:
    settings = get_settings()
    circuit_breaker = get_circuit_breaker_for_account(account_id, settings) if account_id else None
    is_probe = False
    if circuit_breaker is not None:
        is_probe = await circuit_breaker.pre_call_check()
    probe_transferred = False

    request_obj = getattr(session, "request", None)
    if not callable(request_obj):
        try:
            websocket_cm = session.ws_connect(
                url,
                headers=headers,
                receive_timeout=None,
                autoping=True,
                autoclose=True,
                max_msg_size=max_msg_size,
            )
            websocket = await asyncio.wait_for(websocket_cm.__aenter__(), timeout=connect_timeout_seconds)
            if hold_half_open_probe and is_probe and circuit_breaker is not None:
                _bind_half_open_probe(websocket, circuit_breaker)
                probe_transferred = True
            return websocket_cm, websocket
        except Exception as exc:
            if circuit_breaker is not None:
                await _record_account_circuit_breaker_failure(circuit_breaker, exc)
            raise
        finally:
            # Only a successfully opened websocket can own a held probe. A
            # failed connect must release it so recovery can make another try.
            if is_probe and circuit_breaker is not None and not probe_transferred:
                await circuit_breaker.release_half_open_probe()
    request = cast(Callable[..., Awaitable[aiohttp.ClientResponse]], request_obj)

    request_headers = CIMultiDict(headers)
    request_headers.setdefault(hdrs.UPGRADE, "websocket")
    request_headers.setdefault(hdrs.CONNECTION, "Upgrade")
    request_headers.setdefault(hdrs.SEC_WEBSOCKET_VERSION, "13")
    sec_key = base64.b64encode(os.urandom(16)).decode()
    request_headers[hdrs.SEC_WEBSOCKET_KEY] = sec_key

    timeout = aiohttp.ClientTimeout(total=connect_timeout_seconds, sock_connect=connect_timeout_seconds)
    try:
        try:
            resp = await request(
                hdrs.METH_GET,
                url,
                headers=request_headers,
                timeout=timeout,
                read_until_eof=False,
            )
        except Exception as exc:
            if circuit_breaker is not None:
                await _record_account_circuit_breaker_failure(circuit_breaker, exc)
            raise

        async def _raise_handshake_error(message: str) -> None:
            body_text = ""
            try:
                body_text = (await resp.text()).strip()
            except Exception:
                body_text = ""
            raise aiohttp.WSServerHandshakeError(
                resp.request_info,
                resp.history,
                message=body_text or message,
                status=resp.status,
                headers=resp.headers,
            )

        _cb_recorded = False
        try:
            if circuit_breaker is not None:
                if resp.status >= 500:
                    await circuit_breaker._record_failure(Exception(f"WebSocket handshake failed: HTTP {resp.status}"))
                    _cb_recorded = True
                elif resp.status != 101:
                    await circuit_breaker._record_success()
                    _cb_recorded = True

            if resp.status != 101:
                await _raise_handshake_error("Invalid response status")

            if resp.headers.get(hdrs.UPGRADE, "").lower() != "websocket":
                await _raise_handshake_error("Invalid upgrade header")

            if resp.headers.get(hdrs.CONNECTION, "").lower() != "upgrade":
                await _raise_handshake_error("Invalid connection header")

            response_key = resp.headers.get(hdrs.SEC_WEBSOCKET_ACCEPT, "")
            expected_key = base64.b64encode(hashlib.sha1(sec_key.encode() + WS_KEY).digest()).decode()
            if response_key != expected_key:
                await _raise_handshake_error("Invalid challenge response")

            conn = resp.connection
            assert conn is not None
            conn_proto = conn.protocol
            assert conn_proto is not None
            conn_proto.read_timeout = None

            transport = conn.transport
            assert transport is not None
            reader = WebSocketDataQueue(conn_proto, 2**16, loop=session._loop)
            parser = WebSocketReader(reader, max_msg_size, compress=False, decode_text=True)
            conn_proto.set_parser(parser, reader)
            writer = WebSocketWriter(conn_proto, transport, use_mask=True, compress=0, notakeover=False)
        except BaseException as exc:
            if circuit_breaker is not None and not _cb_recorded and isinstance(exc, Exception):
                _cb_recorded = await _record_account_circuit_breaker_failure(circuit_breaker, exc)
            resp.close()
            raise

        websocket = session._ws_response_class(
            reader,
            writer,
            None,
            resp,
            DEFAULT_WS_CLIENT_TIMEOUT,
            True,
            True,
            session._loop,
            heartbeat=None,
            compress=0,
            client_notakeover=False,
        )
        if hold_half_open_probe and is_probe and circuit_breaker is not None:
            _bind_half_open_probe(websocket, circuit_breaker)
            probe_transferred = True
        return websocket, websocket
    finally:
        if is_probe and circuit_breaker is not None and not probe_transferred:
            await circuit_breaker.release_half_open_probe()


async def _stream_websocket_events(
    websocket: aiohttp.ClientWebSocketResponse,
    *,
    idle_timeout_seconds: float,
    total_timeout_seconds: float | None,
    max_event_bytes: int,
    enforce_openai_sdk_contract: bool = True,
) -> AsyncIterator[str]:
    deadline = None if total_timeout_seconds is None else time.monotonic() + total_timeout_seconds

    while True:
        timeout_seconds = idle_timeout_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            timeout_seconds = min(timeout_seconds, remaining)

        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            if deadline is not None and deadline - time.monotonic() <= 0:
                raise
            raise StreamIdleTimeoutError() from exc

        if message.type in {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED}:
            break
        if message.type == aiohttp.WSMsgType.ERROR:
            exc = websocket.exception()
            if exc is None and isinstance(message.data, BaseException):
                exc = message.data
            raise exc or aiohttp.ClientError("Upstream websocket error")
        if message.type not in {aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY}:
            continue

        if message.type == aiohttp.WSMsgType.TEXT:
            text = message.data
        else:
            text = message.data.decode("utf-8", errors="replace")
        text_bytes = text.encode("utf-8")
        if len(text_bytes) > max_event_bytes:
            raise StreamEventTooLargeError(len(text_bytes), max_event_bytes)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        normalized = payload if not enforce_openai_sdk_contract else _normalize_stream_event_payload(payload)
        event_type = normalized.get("type")
        yield format_sse_event(normalized)
        if isinstance(event_type, str) and _is_response_stream_terminal_event_type(
            event_type,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        ):
            break


async def _stream_codex_websocket_events(
    websocket: Any,
    *,
    idle_timeout_seconds: float,
    total_timeout_seconds: float | None,
    max_event_bytes: int,
    enforce_openai_sdk_contract: bool = True,
) -> AsyncIterator[str]:
    deadline = None if total_timeout_seconds is None else time.monotonic() + total_timeout_seconds

    while True:
        timeout_seconds = idle_timeout_seconds
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError
            timeout_seconds = min(timeout_seconds, remaining)

        try:
            msg = await asyncio.wait_for(websocket.receive(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            if deadline is not None and deadline - time.monotonic() <= 0:
                raise
            raise StreamIdleTimeoutError() from exc

        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
            break
        if msg.type == aiohttp.WSMsgType.ERROR:
            exc = websocket.exception()
            if exc is None and isinstance(msg.data, BaseException):
                exc = msg.data
            exc = exc or aiohttp.ClientError("Upstream websocket error")
            raise CodexTransportError(
                codex_transport_error_message("websocket stream", None, exc),
                error_code=process_network_error_code(
                    exc,
                    fallback="upstream_unavailable",
                    include_permanent_dns=False,
                ),
            ) from exc
        if msg.type == aiohttp.WSMsgType.TEXT:
            text = msg.data if isinstance(msg.data, str) else str(msg.data)
        elif msg.type == aiohttp.WSMsgType.BINARY:
            text = msg.data.decode("utf-8", errors="replace") if isinstance(msg.data, bytes) else str(msg.data)
        else:
            text = str(msg.data) if msg.data is not None else ""
        text_bytes = text.encode("utf-8")
        if len(text_bytes) > max_event_bytes:
            raise StreamEventTooLargeError(len(text_bytes), max_event_bytes)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        normalized = payload if not enforce_openai_sdk_contract else _normalize_stream_event_payload(payload)
        event_type = normalized.get("type")
        yield format_sse_event(normalized)
        if isinstance(event_type, str) and _is_response_stream_terminal_event_type(
            event_type,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
        ):
            break


async def _close_unmanaged_websocket(websocket: Any | None) -> None:
    if websocket is None:
        return
    close = getattr(websocket, "close", None)
    if not callable(close):
        return
    result = close()
    if asyncio.iscoroutine(result):
        await result


async def _stream_responses_via_websocket(
    *,
    payload_dict: JsonObject,
    url: str,
    headers: Mapping[str, str],
    client_session: aiohttp.ClientSession,
    effective_total_timeout: float,
    effective_connect_timeout: float,
    effective_idle_timeout: float,
    max_event_bytes: int,
    raise_for_status: bool,
    account_id: str | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = True,
    enforce_openai_sdk_contract: bool = True,
) -> AsyncIterator[str]:
    websocket_url = _to_websocket_upstream_url(url)
    request_started_at = time.monotonic()
    request_payload = _prepare_websocket_response_create_payload(payload_dict)
    websocket_cm: AsyncContextManager[aiohttp.ClientWebSocketResponse] | None = None
    websocket: aiohttp.ClientWebSocketResponse | None = None
    circuit_breaker = None
    lifecycle_recorded = False
    seen_terminal = False
    settings = get_settings()
    if account_id is not None:
        circuit_breaker = get_circuit_breaker_for_account(account_id, settings)

    async def _record_lifecycle_success() -> None:
        nonlocal lifecycle_recorded
        if circuit_breaker is None or lifecycle_recorded:
            return
        await circuit_breaker._record_success()
        lifecycle_recorded = True

    async def _record_lifecycle_failure(exc: Exception) -> None:
        nonlocal lifecycle_recorded
        if circuit_breaker is None or lifecycle_recorded:
            return
        await _record_account_circuit_breaker_failure(circuit_breaker, exc)
        lifecycle_recorded = True

    connect_timeout_seconds = min(
        effective_connect_timeout,
        _remaining_total_timeout(effective_total_timeout, request_started_at, time.monotonic())
        or effective_connect_timeout,
    )
    owns_codex_client = False
    websocket_context: Any | None = None
    active_codex_client: CodexClient | None = None
    if route is not None:
        owns_codex_client = codex_client is None
        active_codex_client = codex_client or CodexClient(create_codex_session())
        try:
            opener = getattr(active_codex_client, "open_ws_with_route_metadata", None)
            if callable(opener):
                result = await opener(
                    websocket_url,
                    route=route,
                    headers=headers,
                    timeout=connect_timeout_seconds,
                    max_msg_size=max_event_bytes,
                )
                websocket = result.websocket
                websocket_context = result.context
                if route_trace is not None:
                    route_trace.record(route=result.route, fallback_used=result.fallback_used)
            else:
                websocket_context = await active_codex_client.ws_connect(
                    websocket_url,
                    route=route,
                    headers=headers,
                    timeout=connect_timeout_seconds,
                    max_msg_size=max_event_bytes,
                )
                websocket = (
                    await websocket_context.__aenter__()
                    if hasattr(websocket_context, "__aenter__")
                    else websocket_context
                )
                if route_trace is not None:
                    route_trace.record(route=route, fallback_used=False)
                if not hasattr(websocket_context, "__aenter__"):
                    websocket_context = None
        except Exception as exc:
            await _record_lifecycle_failure(exc)
            if owns_codex_client:
                await active_codex_client.close()
            error_code = (
                exc.error_code
                if isinstance(exc, CodexTransportError) and exc.error_code is not None
                else process_network_error_code(
                    exc,
                    fallback="upstream_unavailable",
                    include_permanent_dns=False,
                )
            )
            if error_code == PROCESS_NETWORK_UNAVAILABLE_CODE and not (
                isinstance(exc, CodexTransportError) and exc.retryable_same_contract
            ):
                raise CodexTransportError(
                    codex_transport_error_message("websocket connect", route.endpoint_id, exc),
                    status_code=exc.status_code if isinstance(exc, CodexTransportError) else None,
                    error_code=error_code,
                    retryable_same_contract=True,
                ) from exc
            raise
    else:
        try:
            websocket_cm, websocket = await _open_upstream_websocket(
                session=client_session,
                url=websocket_url,
                headers=headers,
                connect_timeout_seconds=connect_timeout_seconds,
                max_msg_size=max_event_bytes,
                account_id=account_id,
                hold_half_open_probe=True,
            )
        except Exception as exc:
            error_code = process_network_error_code(
                exc,
                fallback="upstream_unavailable",
                include_permanent_dns=not is_proxy_endpoint_failure(exc),
            )
            if error_code == PROCESS_NETWORK_UNAVAILABLE_CODE:
                raise CodexTransportError(
                    codex_transport_error_message("websocket connect", None, exc),
                    error_code=error_code,
                    retryable_same_contract=True,
                ) from exc
            raise

    try:
        send_json = getattr(websocket, "send_json", None)
        remaining_total_timeout = _remaining_total_timeout(
            effective_total_timeout,
            request_started_at,
            time.monotonic(),
        )
        archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="websocket",
            payload=request_payload,
            account_id=account_id,
            method="GET",
            url=websocket_url,
            headers=headers,
            extra={"frame_type": "text"},
        )
        if callable(send_json):
            await asyncio.wait_for(
                cast(Callable[[JsonObject], Awaitable[None]], send_json)(request_payload),
                timeout=remaining_total_timeout,
            )
        elif route is not None:
            payload_text = json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))
            send_str = getattr(websocket, "send_str", None)
            if callable(send_str):
                send_result = send_str(payload_text)
                if asyncio.iscoroutine(send_result):
                    await asyncio.wait_for(send_result, timeout=remaining_total_timeout)
            else:
                send_bytes = getattr(websocket, "send_bytes")
                send_result = send_bytes(payload_text.encode())
                if asyncio.iscoroutine(send_result):
                    await asyncio.wait_for(send_result, timeout=remaining_total_timeout)
        else:
            await asyncio.wait_for(
                websocket.send_str(json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))),
                timeout=remaining_total_timeout,
            )
        remaining_total_timeout = _remaining_total_timeout(
            effective_total_timeout,
            request_started_at,
            time.monotonic(),
        )
        event_iter = (
            _stream_codex_websocket_events(
                websocket,
                idle_timeout_seconds=effective_idle_timeout,
                total_timeout_seconds=remaining_total_timeout,
                max_event_bytes=max_event_bytes,
                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            )
            if route is not None
            else _stream_websocket_events(
                websocket,
                idle_timeout_seconds=effective_idle_timeout,
                total_timeout_seconds=remaining_total_timeout,
                max_event_bytes=max_event_bytes,
                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            )
        )
        async for event in event_iter:
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=event,
                account_id=account_id,
                method="GET",
                url=websocket_url,
                headers=headers,
                extra={"event_format": "sse"},
            )
            parsed_event = parse_sse_event(event)
            if parsed_event and _is_response_stream_terminal_event_type(
                parsed_event.type,
                enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            ):
                seen_terminal = True
                await _record_lifecycle_success()
            yield event
        if not seen_terminal:
            await _record_lifecycle_failure(aiohttp.ClientError("Upstream websocket closed without terminal event"))
    except Exception as exc:
        await _record_lifecycle_failure(exc)
        raise
    finally:
        try:
            if websocket_context is not None:
                await websocket_context.__aexit__(None, None, None)
            elif websocket_cm is not None:
                await websocket_cm.__aexit__(None, None, None)
            else:
                await _close_unmanaged_websocket(websocket)
        finally:
            if owns_codex_client and active_codex_client is not None:
                await active_codex_client.close()
            await _release_bound_half_open_probe(websocket)


def _build_websocket_response_create_payload(payload_dict: JsonObject) -> JsonObject:
    request_payload: JsonObject = {
        key: value for key, value in payload_dict.items() if key not in _WEBSOCKET_RESPONSE_CREATE_EXCLUDED_FIELDS
    }
    request_payload["type"] = "response.create"
    return request_payload


def _prepare_websocket_response_create_payload(payload_dict: JsonObject) -> JsonObject:
    request_payload = _build_websocket_response_create_payload(payload_dict)
    payload_text = json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))
    payload_size = len(payload_text.encode("utf-8"))
    if payload_size > _UPSTREAM_RESPONSE_CREATE_MAX_BYTES:
        slimmed_payload, slim_summary = _slim_response_create_payload_for_upstream(
            request_payload,
            max_bytes=_UPSTREAM_RESPONSE_CREATE_MAX_BYTES,
        )
        if slim_summary is not None:
            request_payload = slimmed_payload
            slimmed_text = json.dumps(request_payload, ensure_ascii=True, separators=(",", ":"))
            logger.warning(
                (
                    "Slimmed response.create before upstream websocket connect request_id=%s "
                    "original_bytes=%s slimmed_bytes=%s historical_tool_outputs_slimmed=%s "
                    "historical_images_slimmed=%s"
                ),
                get_request_id(),
                payload_size,
                len(slimmed_text.encode("utf-8")),
                slim_summary["historical_tool_outputs_slimmed"],
                slim_summary["historical_images_slimmed"],
            )
            payload_text = slimmed_text
            payload_size = len(payload_text.encode("utf-8"))
    if payload_size > _UPSTREAM_RESPONSE_CREATE_WARN_BYTES:
        previous_response_id = request_payload.get("previous_response_id")
        logger.warning(
            "Large response.create prepared request_id=%s bytes=%s previous_response_id=%s",
            get_request_id(),
            payload_size,
            previous_response_id if isinstance(previous_response_id, str) else None,
        )
    if payload_size <= _UPSTREAM_RESPONSE_CREATE_MAX_BYTES:
        return request_payload
    # 400, not 413: the Codex client surfaces 400 immediately as a non-retryable
    # invalid request, while 413 burns five full-payload retries and then pins the
    # session to HTTP transport.
    raise ProxyResponseError(
        400,
        _response_create_too_large_error_envelope(payload_size, _UPSTREAM_RESPONSE_CREATE_MAX_BYTES),
        failure_phase="validation",
        failure_detail=f"response.create_bytes={payload_size}",
    )


def _response_create_too_large_error_envelope(actual_bytes: int, max_bytes: int) -> OpenAIErrorEnvelope:
    payload = openai_error(
        "payload_too_large",
        (
            "response.create is too large for upstream websocket "
            f"({actual_bytes} bytes > {max_bytes} bytes). "
            "Reduce historical images/screenshots or compact the thread."
        ),
        error_type="invalid_request_error",
    )
    payload["error"]["param"] = "input"
    return payload


def _slim_response_create_payload_for_upstream(
    payload: JsonObject,
    *,
    max_bytes: int,
) -> tuple[JsonObject, dict[str, int] | None]:
    del max_bytes
    input_value = payload.get("input")
    if not isinstance(input_value, list) or not input_value:
        return payload, None

    input_items = cast(list[JsonValue], deepcopy(input_value))
    preserve_from = _response_create_recent_suffix_start(input_items)
    historical = input_items[:preserve_from]
    recent = input_items[preserve_from:]

    tool_outputs_slimmed = 0
    images_slimmed = 0

    slimmed_historical: list[JsonValue] = []
    for item in historical:
        slimmed_item, item_tool_outputs_slimmed, item_images_slimmed = _slim_historical_response_input_item(item)
        tool_outputs_slimmed += item_tool_outputs_slimmed
        images_slimmed += item_images_slimmed
        slimmed_historical.append(slimmed_item)

    if tool_outputs_slimmed == 0 and images_slimmed == 0:
        return payload, None

    candidate_payload = dict(payload)
    candidate_payload["input"] = slimmed_historical + recent
    return candidate_payload, {
        "historical_tool_outputs_slimmed": tool_outputs_slimmed,
        "historical_images_slimmed": images_slimmed,
    }


def _response_create_recent_suffix_start(input_items: list[JsonValue]) -> int:
    last_user_index: int | None = None
    for index, item in enumerate(input_items):
        if not is_json_mapping(item):
            continue
        if item.get("role") == "user":
            last_user_index = index
    if last_user_index is not None:
        return last_user_index
    return 0


def _slim_historical_response_input_item(item: JsonValue) -> tuple[JsonValue, int, int]:
    if not is_json_mapping(item):
        return item, 0, 0

    item_mapping = dict(cast(dict[str, JsonValue], deepcopy(item)))
    tool_outputs_slimmed = 0
    images_slimmed = 0

    item_type = item_mapping.get("type")
    if isinstance(item_type, str) and item_type in _SLIMMABLE_TOOL_CALL_OUTPUT_ITEM_TYPES:
        output = item_mapping.get("output")
        if isinstance(output, str):
            if _should_slim_historical_tool_output(output):
                item_mapping["output"] = _RESPONSE_CREATE_TOOL_OUTPUT_OMISSION_NOTICE.format(
                    bytes=len(output.encode("utf-8"))
                )
                tool_outputs_slimmed += 1
        else:
            slimmed_output, output_images_slimmed = _slim_historical_response_content(output)
            if output_images_slimmed > 0:
                item_mapping["output"] = slimmed_output
                images_slimmed += output_images_slimmed

    content = item_mapping.get("content")
    slimmed_content, content_images_slimmed = _slim_historical_response_content(content)
    if content_images_slimmed > 0:
        item_mapping["content"] = slimmed_content
        images_slimmed += content_images_slimmed

    if item_type == "input_image" and _is_inline_image_reference(item_mapping.get("image_url")):
        return _response_create_inline_image_notice_item(), tool_outputs_slimmed, images_slimmed + 1

    return item_mapping, tool_outputs_slimmed, images_slimmed


def _slim_historical_response_content(content: JsonValue) -> tuple[JsonValue, int]:
    if is_json_mapping(content):
        return _slim_historical_response_content_part(content)
    if not isinstance(content, list):
        return content, 0

    slimmed_parts: list[JsonValue] = []
    images_slimmed = 0
    for part in content:
        slimmed_part, part_images_slimmed = _slim_historical_response_content_part(part)
        slimmed_parts.append(slimmed_part)
        images_slimmed += part_images_slimmed
    return slimmed_parts, images_slimmed


def _slim_historical_response_content_part(part: JsonValue) -> tuple[JsonValue, int]:
    if not is_json_mapping(part):
        return part, 0

    part_mapping = dict(cast(dict[str, JsonValue], deepcopy(part)))
    part_type = part_mapping.get("type")
    if part_type == "input_image" and _is_inline_image_reference(part_mapping.get("image_url")):
        return _response_create_inline_image_notice_part(), 1

    if part_type == "image_url":
        image_url_value = part_mapping.get("image_url")
        if is_json_mapping(image_url_value):
            image_url = image_url_value.get("url")
        else:
            image_url = image_url_value
        if _is_inline_image_reference(image_url):
            return _response_create_inline_image_notice_part(), 1

    return part_mapping, 0


def _response_create_inline_image_notice_part() -> JsonObject:
    return {"type": "input_text", "text": _RESPONSE_CREATE_IMAGE_OMISSION_NOTICE}


def _response_create_inline_image_notice_item() -> JsonObject:
    return {"role": "user", "content": [_response_create_inline_image_notice_part()]}


def _is_inline_image_reference(value: JsonValue) -> bool:
    return isinstance(value, str) and value.startswith("data:image/")


def _should_slim_historical_tool_output(output: str) -> bool:
    return "data:image/" in output or len(output.encode("utf-8")) > 32 * 1024


async def _inline_input_image_urls(
    payload: JsonObject,
    session: "ImageFetchSession",
    connect_timeout: float,
) -> dict[str, JsonValue]:
    payload_dict = dict(payload)
    input_value = payload_dict.get("input")
    if not isinstance(input_value, list):
        return payload_dict
    updated_input: list[JsonValue] = []
    changed = False
    for item in input_value:
        if not isinstance(item, dict):
            updated_input.append(item)
            continue
        if item.get("type") == "input_image":
            updated_item, item_changed = await _inline_content_images(item, session, connect_timeout)
            updated_input.append(updated_item)
            changed = changed or item_changed
            continue
        content = item.get("content")
        updated_content, content_changed = await _inline_content_images(content, session, connect_timeout)
        if content_changed:
            new_item = dict(item)
            new_item["content"] = updated_content
            updated_input.append(new_item)
            changed = True
        else:
            updated_input.append(item)
    if not changed:
        return payload_dict
    payload_dict["input"] = updated_input
    return payload_dict


async def _inline_content_images(
    content: JsonValue,
    session: "ImageFetchSession",
    connect_timeout: float,
) -> tuple[JsonValue, bool]:
    if content is None:
        return content, False
    parts = content if isinstance(content, list) else [content]
    updated_parts: list[JsonValue] = []
    changed = False
    for part in parts:
        if not isinstance(part, dict):
            updated_parts.append(part)
            continue
        part_type = part.get("type")
        image_url = part.get("image_url") if part_type == "input_image" else None
        if isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
            data_url = await _fetch_image_data_url(session, image_url, connect_timeout)
            if data_url:
                new_part = dict(part)
                new_part["image_url"] = data_url
                updated_parts.append(new_part)
                changed = True
                continue
        updated_parts.append(part)
    if isinstance(content, list):
        return updated_parts, changed
    return (updated_parts[0] if updated_parts else ""), changed


async def _fetch_image_data_url(
    session: "ImageFetchSession",
    image_url: str,
    connect_timeout: float,
) -> str | None:
    target = await _resolve_safe_image_fetch_target(image_url, connect_timeout=connect_timeout)
    if target is None:
        return None
    timeout = aiohttp.ClientTimeout(
        total=_IMAGE_INLINE_TIMEOUT_SECONDS,
        sock_connect=connect_timeout,
        sock_read=_IMAGE_INLINE_TIMEOUT_SECONDS,
    )
    headers = {"Host": target.host_header}
    for request_url in target.request_urls:
        try:
            async with session.get(
                request_url,
                timeout=timeout,
                allow_redirects=False,
                headers=headers,
                server_hostname=target.server_hostname,
            ) as resp:
                if resp.status != 200:
                    continue
                content_type = resp.headers.get("Content-Type")
                mime_type = content_type.split(";", 1)[0].strip() if isinstance(content_type, str) else ""
                if not mime_type:
                    mime_type = "application/octet-stream"
                data = bytearray()
                async for chunk in resp.content.iter_chunked(_IMAGE_INLINE_CHUNK_SIZE):
                    if not chunk:
                        continue
                    data.extend(chunk)
                    if len(data) > _IMAGE_INLINE_MAX_BYTES:
                        return None
                if not data:
                    continue
                encoded = base64.b64encode(data).decode("ascii")
                return f"data:{mime_type};base64,{encoded}"
        except (aiohttp.ClientError, asyncio.TimeoutError):
            continue
    return None


@dataclass(slots=True, frozen=True)
class SafeImageFetchTarget:
    request_urls: tuple[str, ...]
    host_header: str
    server_hostname: str


def _build_pinned_request_url(parsed: ParseResult, resolved_ip: str) -> str:
    path = parsed.path or "/"
    ip_host = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
    try:
        parsed_port = parsed.port
    except ValueError:
        parsed_port = None
    netloc = f"{ip_host}:{parsed_port}" if parsed_port is not None else ip_host
    return urlunparse((parsed.scheme, netloc, path, parsed.params, parsed.query, parsed.fragment))


async def _resolve_safe_image_fetch_target(
    url: str,
    *,
    connect_timeout: float,
) -> SafeImageFetchTarget | None:
    settings = get_settings()
    if not settings.image_inline_fetch_enabled:
        return None

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None
    if parsed.username or parsed.password:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    host = hostname.strip().lower().rstrip(".")
    if not host:
        return None
    if host in _BLOCKED_LITERAL_HOSTS:
        return None

    allowed_hosts = settings.image_inline_allowed_hosts
    if allowed_hosts and host not in allowed_hosts:
        return None

    literal_ip = _parse_ip_literal(host)
    if literal_ip is not None:
        if _is_disallowed_ip(literal_ip):
            return None
        resolved_ips = [literal_ip.compressed]
    else:
        resolve_timeout = min(connect_timeout, _IMAGE_INLINE_TIMEOUT_SECONDS)
        resolved_ips = await _resolve_global_ips(host, timeout_seconds=resolve_timeout)
        if not resolved_ips:
            return None

    request_urls = tuple(_build_pinned_request_url(parsed, resolved_ip) for resolved_ip in resolved_ips)
    if not request_urls:
        return None

    try:
        parsed_port = parsed.port
    except ValueError:
        return None
    host_header = host if parsed_port in (None, 443) else f"{host}:{parsed_port}"
    return SafeImageFetchTarget(
        request_urls=request_urls,
        host_header=host_header,
        server_hostname=host,
    )


async def _is_safe_image_fetch_url(url: str, *, connect_timeout: float) -> bool:
    target = await _resolve_safe_image_fetch_target(url, connect_timeout=connect_timeout)
    return target is not None


def _parse_ip_literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def _is_blocked_ip_literal(host: str) -> bool:
    ip = _parse_ip_literal(host)
    if ip is None:
        return False
    return _is_disallowed_ip(ip)


async def _resolve_global_ips(host: str, *, timeout_seconds: float) -> list[str] | None:
    loop = asyncio.get_running_loop()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, proto=socket.IPPROTO_TCP),
            timeout=timeout_seconds,
        )
    except (OSError, asyncio.TimeoutError):
        return None
    if not infos:
        return None

    resolved_ips: list[str] = []
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            return None
        addr = sockaddr[0]
        if not isinstance(addr, str):
            return None
        ip = _parse_ip_literal(addr)
        if ip is None:
            return None
        if _is_disallowed_ip(ip):
            return None
        normalized_ip = ip.compressed
        if normalized_ip in seen:
            continue
        seen.add(normalized_ip)
        resolved_ips.append(normalized_ip)
    return resolved_ips or None


async def _resolves_to_blocked_ip(host: str, *, timeout_seconds: float) -> bool:
    resolved_ips = await _resolve_global_ips(host, timeout_seconds=timeout_seconds)
    return resolved_ips is None


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_multicast:
        return True
    return not ip.is_global


class ImageFetchContent(Protocol):
    def iter_chunked(self, size: int) -> AsyncIterator[bytes]: ...


class ImageFetchResponse(Protocol):
    status: int
    headers: Mapping[str, str]
    content: ImageFetchContent


class ImageFetchSession(Protocol):
    def get(
        self,
        url: str,
        timeout: aiohttp.ClientTimeout,
        *,
        allow_redirects: bool = False,
        headers: Mapping[str, str] | None = None,
        server_hostname: str | None = None,
    ) -> AsyncContextManager[ImageFetchResponse]: ...


def _as_image_fetch_session(session: aiohttp.ClientSession) -> ImageFetchSession:
    return cast(ImageFetchSession, session)


async def stream_responses(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    raise_for_status: bool = False,
    session: aiohttp.ClientSession | None = None,
    upstream_stream_transport_override: str | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = True,
    codex_installation_id: str | None = None,
    enforce_openai_sdk_contract: bool = True,
    codex_lb_account_id: str | None = None,
    suppress_live_usage: bool = False,
) -> AsyncIterator[str]:
    effective_allow_direct_egress = allow_direct_egress or (route is None and session is not None)
    async with lease_http_session(session) as client_session:
        async for event_block in _stream_responses_with_session(
            payload=payload,
            headers=headers,
            access_token=access_token,
            account_id=account_id,
            base_url=base_url,
            raise_for_status=raise_for_status,
            session=client_session,
            upstream_stream_transport_override=upstream_stream_transport_override,
            route=route,
            codex_client=codex_client,
            route_trace=route_trace,
            allow_direct_egress=effective_allow_direct_egress,
            codex_installation_id=codex_installation_id,
            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
            codex_lb_account_id=codex_lb_account_id,
            suppress_live_usage=suppress_live_usage,
        ):
            if not suppress_live_usage and (codex_lb_account_id or account_id) and EVENT_MARKER in event_block:
                publish_live_usage(
                    parse_rate_limit_event_text(event_block),
                    account_id=codex_lb_account_id,
                    chatgpt_account_id=None if codex_lb_account_id else account_id,
                )
            yield event_block


async def _stream_responses_with_session(
    payload: ResponsesRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    session: aiohttp.ClientSession,
    base_url: str | None = None,
    raise_for_status: bool = False,
    upstream_stream_transport_override: str | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = True,
    codex_installation_id: str | None = None,
    enforce_openai_sdk_contract: bool = True,
    codex_lb_account_id: str | None = None,
    suppress_live_usage: bool = False,
) -> AsyncIterator[str]:
    settings = get_settings()
    headers = apply_codex_installation_headers(headers, codex_installation_id)
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/codex/responses"
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="responses stream",
    )
    if route is None and route_trace is not None:
        route_trace.record_direct()
    pre_request_started_at = time.monotonic()
    # Keep a default total timeout so direct callers cannot hang forever before
    # response headers or the first SSE event. Responses streams can run much
    # longer than control calls; ProxyService stream attempts clamp this further
    # by installing per-attempt overrides from the remaining budget.
    request_total_timeout = _effective_stream_timeout(
        getattr(
            settings,
            "http_responses_stream_request_budget_seconds",
            settings.proxy_request_budget_seconds,
        ),
        "total",
    )
    effective_connect_timeout = _effective_stream_timeout(settings.upstream_connect_timeout_seconds, "connect")
    effective_idle_timeout = _effective_stream_timeout(settings.stream_idle_timeout_seconds, "idle")

    seen_terminal = False
    status_code: int | None = None
    last_stream_activity_at: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    failure_phase: str | None = None
    failure_detail: str | None = None
    failure_exception_type: str | None = None
    retryable_same_contract: bool | None = None
    client_session = session
    payload_dict = dict(payload.to_payload())
    apply_codex_installation_metadata(payload_dict, codex_installation_id)
    if settings.image_inline_fetch_enabled:
        payload_dict = await _inline_input_image_urls(
            payload_dict,
            _as_image_fetch_session(client_session),
            effective_connect_timeout,
        )
    http_payload_dict = dict(payload_dict)
    _strip_responses_lite_websocket_client_metadata(http_payload_dict)
    _finalize_responses_lite_reasoning_context(
        http_payload_dict,
        responses_lite=_payload_uses_responses_lite(http_payload_dict),
    )
    websocket_payload_dict = dict(payload_dict)
    _set_responses_lite_websocket_client_metadata(websocket_payload_dict)
    _finalize_responses_lite_reasoning_context(
        websocket_payload_dict,
        responses_lite=_payload_has_responses_lite_websocket_marker(websocket_payload_dict),
    )
    payload_json = json.dumps(websocket_payload_dict, ensure_ascii=True, separators=(",", ":"))
    payload_size_estimate_bytes = len(payload_json.encode("utf-8"))
    transport_mode = _configured_stream_transport(
        transport=settings.upstream_stream_transport,
        transport_override=upstream_stream_transport_override,
    )
    transport = _resolve_stream_transport(
        settings=settings,
        transport=settings.upstream_stream_transport,
        transport_override=upstream_stream_transport_override,
        model=payload.model,
        headers=headers,
        has_image_generation_tool=_payload_uses_image_generation_tool(payload_dict),
        payload_size_estimate_bytes=payload_size_estimate_bytes,
    )
    payload_dict = websocket_payload_dict if transport == "websocket" else http_payload_dict
    payload_json = json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
    if transport == "websocket":
        upstream_headers = _build_upstream_websocket_headers(headers, access_token, account_id)
        method = "GET"
    else:
        upstream_headers = _build_upstream_headers(headers, access_token, account_id)
        _apply_responses_lite_http_header(upstream_headers, payload_dict)
        method = "POST"
    upstream_headers = apply_codex_installation_headers(upstream_headers, codex_installation_id)
    remaining_request_timeout = _remaining_total_timeout(
        request_total_timeout,
        pre_request_started_at,
        time.monotonic(),
    )
    timeout = aiohttp.ClientTimeout(
        total=remaining_request_timeout,
        sock_connect=effective_connect_timeout,
        sock_read=None,
    )
    started_at = time.monotonic()

    async def _stream_via_http(
        current_headers: Mapping[str, str],
        current_timeout: aiohttp.ClientTimeout,
    ) -> AsyncIterator[str]:
        nonlocal status_code, last_stream_activity_at, error_code, error_message, seen_terminal

        if route is not None:
            owns_codex_client = codex_client is None
            active_codex_client = codex_client or CodexClient(create_codex_session())
            try:
                request_kwargs: dict[str, Any] = {
                    "json": payload_dict,
                    "headers": current_headers,
                    "timeout": remaining_request_timeout or request_total_timeout,
                    "buffer_response": False,
                }
                request_with_metadata = getattr(active_codex_client, "request_with_route_metadata", None)
                if callable(request_with_metadata):
                    result = await request_with_metadata("POST", url, route=route, **request_kwargs)
                    raw_resp = result.response
                    if route_trace is not None:
                        route_trace.record(route=result.route, fallback_used=result.fallback_used)
                else:
                    raw_resp = await active_codex_client.request("POST", url, route=route, **request_kwargs)
                    if route_trace is not None:
                        route_trace.record(route=route, fallback_used=False)
                resp = _CodexSSEResponse(raw_resp)
                status_code = resp.status
                last_stream_activity_at = time.monotonic()
                # Error responses (429/403) carry the saturated-window
                # snapshot — exactly when freshness matters most — so headers
                # are ingested regardless of status.
                if not suppress_live_usage:
                    publish_live_usage(
                        parse_rate_limit_headers(getattr(raw_resp, "headers", None)),
                        account_id=codex_lb_account_id,
                        chatgpt_account_id=None if codex_lb_account_id else account_id,
                    )
                if resp.status >= 400:
                    if raise_for_status:
                        error_payload = await _error_payload_from_response(resp)
                        error_code, error_message = _error_details_from_envelope(error_payload)
                        archive_json(
                            direction="server_to_codex",
                            kind="responses",
                            transport="http",
                            payload=error_payload,
                            account_id=account_id,
                            method="POST",
                            url=url,
                            status_code=status_code,
                            headers=current_headers,
                        )
                        raise ProxyResponseError(resp.status, error_payload)
                    event = await _error_event_from_response(resp)
                    error_code, error_message = _error_details_from_failed_event(event)
                    event_block = format_sse_event(event)
                    archive_text(
                        direction="server_to_codex",
                        kind="responses",
                        transport="http",
                        text=event_block,
                        account_id=account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=current_headers,
                        extra={"event_format": "sse"},
                    )
                    yield event_block
                    return

                async for event_block in _iter_sse_events(
                    cast(SSEResponse, resp),
                    effective_idle_timeout,
                    settings.max_sse_event_bytes,
                ):
                    last_stream_activity_at = time.monotonic()
                    event_block = _normalize_sse_event_block(event_block)
                    event_block, normalized_event_type = _normalize_stream_payload_for_http_block(
                        event_block,
                        enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                    )
                    event = parse_sse_event(event_block)
                    if event:
                        if event.type in _RESPONSE_STREAM_TERMINAL_EVENT_TYPES or (
                            event.type == "error" and not enforce_openai_sdk_contract
                        ):
                            seen_terminal = True
                    elif isinstance(normalized_event_type, str) and (
                        normalized_event_type in _RESPONSE_STREAM_TERMINAL_EVENT_TYPES
                        or (normalized_event_type == "error" and not enforce_openai_sdk_contract)
                    ):
                        seen_terminal = True
                    archive_text(
                        direction="server_to_codex",
                        kind="responses",
                        transport="http",
                        text=event_block,
                        account_id=account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=current_headers,
                        extra={"event_format": "sse"},
                    )
                    yield event_block
                    if seen_terminal:
                        break
                return
            finally:
                if owns_codex_client:
                    await active_codex_client.close()

        async with _service_circuit_breaker_context(
            client_session.post(
                url,
                json=payload_dict,
                headers=current_headers,
                timeout=current_timeout,
            ),
            settings=settings,
            account_id=account_id,
        ) as resp:
            status_code = resp.status
            last_stream_activity_at = time.monotonic()
            if not suppress_live_usage:
                publish_live_usage(
                    parse_rate_limit_headers(getattr(resp, "headers", None)),
                    account_id=codex_lb_account_id,
                    chatgpt_account_id=None if codex_lb_account_id else account_id,
                )
            if resp.status >= 400:
                if raise_for_status:
                    error_payload = await _error_payload_from_response(resp)
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    archive_json(
                        direction="server_to_codex",
                        kind="responses",
                        transport="http",
                        payload=error_payload,
                        account_id=account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=current_headers,
                    )
                    raise ProxyResponseError(resp.status, error_payload)
                event = await _error_event_from_response(resp)
                error_code, error_message = _error_details_from_failed_event(event)
                event_block = format_sse_event(event)
                archive_text(
                    direction="server_to_codex",
                    kind="responses",
                    transport="http",
                    text=event_block,
                    account_id=account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=current_headers,
                    extra={"event_format": "sse"},
                )
                yield event_block
                return

            async for event_block in _iter_sse_events(
                resp,
                effective_idle_timeout,
                settings.max_sse_event_bytes,
            ):
                last_stream_activity_at = time.monotonic()
                event_block = _normalize_sse_event_block(event_block)
                event_block, normalized_event_type = _normalize_stream_payload_for_http_block(
                    event_block,
                    enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                )
                event = parse_sse_event(event_block)
                if event:
                    if event.type in _RESPONSE_STREAM_TERMINAL_EVENT_TYPES or (
                        event.type == "error" and not enforce_openai_sdk_contract
                    ):
                        seen_terminal = True
                elif isinstance(normalized_event_type, str) and (
                    normalized_event_type in _RESPONSE_STREAM_TERMINAL_EVENT_TYPES
                    or (normalized_event_type == "error" and not enforce_openai_sdk_contract)
                ):
                    seen_terminal = True
                archive_text(
                    direction="server_to_codex",
                    kind="responses",
                    transport="http",
                    text=event_block,
                    account_id=account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=current_headers,
                    extra={"event_format": "sse"},
                )
                yield event_block
                if seen_terminal:
                    break

    _maybe_log_upstream_request_start(
        kind="responses",
        url=url,
        headers=upstream_headers,
        method=method,
        payload_summary=_summarize_json_payload(payload_dict),
        payload_json=payload_json if "upstream_payload" in settings.trace_channels else None,
    )
    if transport == "http":
        archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload=payload_dict,
            account_id=account_id,
            method=method,
            url=url,
            headers=upstream_headers,
        )

    async def _stream_via_http_after_websocket_rejection(
        *,
        rejection_status: int | None,
        rejection_message: str,
    ) -> AsyncIterator[str]:
        nonlocal transport, upstream_headers, method, remaining_request_timeout, timeout, started_at, payload_dict
        nonlocal payload_json

        logger.warning(
            "upstream_websocket_handshake_rejected request_id=%s status=%s target=%s retrying_transport=http",
            get_request_id(),
            rejection_status,
            _summarize_upstream_target(url),
        )
        _maybe_log_upstream_request_complete(
            kind="responses",
            url=url,
            headers=upstream_headers,
            method=method,
            started_at=started_at,
            status_code=rejection_status,
            error_code="upstream_websocket_handshake_rejected",
            error_message=rejection_message,
        )

        transport = "http"
        payload_dict = http_payload_dict
        payload_json = json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
        upstream_headers = _build_upstream_headers(headers, access_token, account_id)
        _apply_responses_lite_http_header(upstream_headers, payload_dict)
        upstream_headers = apply_codex_installation_headers(upstream_headers, codex_installation_id)
        method = "POST"
        remaining_request_timeout = _remaining_total_timeout(
            request_total_timeout,
            pre_request_started_at,
            time.monotonic(),
        )
        timeout = aiohttp.ClientTimeout(
            total=remaining_request_timeout,
            sock_connect=effective_connect_timeout,
            sock_read=None,
        )
        started_at = time.monotonic()
        _maybe_log_upstream_request_start(
            kind="responses",
            url=url,
            headers=upstream_headers,
            method=method,
            payload_summary=_summarize_json_payload(payload_dict),
            payload_json=payload_json if "upstream_payload" in settings.trace_channels else None,
        )
        archive_json(
            direction="codex_to_server",
            kind="responses",
            transport="http",
            payload=payload_dict,
            account_id=account_id,
            method=method,
            url=url,
            headers=upstream_headers,
        )
        async for event_block in _stream_via_http(upstream_headers, timeout):
            yield event_block

    try:
        if transport == "websocket":
            try:
                async for event_block in _stream_responses_via_websocket(
                    payload_dict=payload_dict,
                    url=url,
                    headers=upstream_headers,
                    client_session=client_session,
                    effective_total_timeout=(remaining_request_timeout or settings.proxy_request_budget_seconds),
                    effective_connect_timeout=effective_connect_timeout,
                    effective_idle_timeout=effective_idle_timeout,
                    max_event_bytes=settings.max_sse_event_bytes,
                    raise_for_status=raise_for_status,
                    account_id=account_id,
                    route=route,
                    codex_client=codex_client,
                    route_trace=route_trace,
                    enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                ):
                    if status_code is None:
                        status_code = 101
                    event = parse_sse_event(event_block)
                    if event:
                        event_type = event.type
                        if _is_response_stream_terminal_event_type(
                            event_type,
                            enforce_openai_sdk_contract=enforce_openai_sdk_contract,
                        ):
                            seen_terminal = True
                    yield event_block
            except aiohttp.WSServerHandshakeError as exc:
                if not _should_fallback_to_http_after_websocket_handshake_error(transport_mode, exc):
                    error_payload = _error_payload_from_websocket_handshake_error(exc)
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    status_code = exc.status
                    if error_message is None:
                        error_message = exc.message or str(exc)
                    if error_code is None:
                        error_code = "upstream_error"
                    response_error_code = cast(str, error_code)
                    response_error_message = cast(str, error_message)
                    if raise_for_status:
                        raise ProxyResponseError(exc.status, error_payload) from exc
                    yield format_sse_event(
                        response_failed_event(response_error_code, response_error_message, response_id=get_request_id())
                    )
                    return

                async for event_block in _stream_via_http_after_websocket_rejection(
                    rejection_status=exc.status,
                    rejection_message=str(exc),
                ):
                    yield event_block
            except CodexTransportError as exc:
                if not _should_fallback_to_http_after_websocket_status(transport_mode, exc.status_code):
                    raise
                async for event_block in _stream_via_http_after_websocket_rejection(
                    rejection_status=exc.status_code,
                    rejection_message=str(exc),
                ):
                    yield event_block
        else:
            async for event_block in _stream_via_http(upstream_headers, timeout):
                yield event_block
    except ProxyResponseError as exc:
        status_code = exc.status_code
        raise
    except StreamIdleTimeoutError:
        error_code = "stream_idle_timeout"
        error_message = "Upstream stream idle timeout"
        failure_phase = "upstream"
        failure_detail = "stream_idle_timeout"
        failure_exception_type = "StreamIdleTimeoutError"
        retryable_same_contract = False
        yield format_sse_event(
            response_failed_event(
                "stream_idle_timeout",
                "Upstream stream idle timeout",
                response_id=get_request_id(),
            ),
        )
        return
    except StreamEventTooLargeError as exc:
        error_code = "stream_event_too_large"
        error_message = str(exc)
        yield format_sse_event(
            response_failed_event(
                "stream_event_too_large",
                str(exc),
                response_id=get_request_id(),
            ),
        )
        return
    except CircuitBreakerOpenError:
        error_code = "upstream_unavailable"
        error_message = "Upstream circuit breaker is open"
        yield format_sse_event(
            response_failed_event(
                "upstream_unavailable",
                "Upstream circuit breaker is open",
                response_id=get_request_id(),
            ),
        )
        return
    except CodexTransportError as exc:
        routed_error_code = exc.error_code or "upstream_unavailable"
        error_code = routed_error_code
        error_message = _codex_route_transport_error_message(
            route=route,
            route_trace=route_trace,
            operation="stream",
            exc=exc,
        )
        response_error_message = cast(str, error_message)
        failure_phase = exc.failure_phase or "upstream"
        failure_detail = "transport_error"
        failure_exception_type = type(exc).__name__
        retryable_same_contract = exc.retryable_same_contract
        if routed_error_code == PROCESS_NETWORK_UNAVAILABLE_CODE and exc.retryable_same_contract:
            # Routed Codex sessions are private to this attempt, so recovery
            # legitimately has no shared HTTP generation for compare-and-swap.
            raise _process_network_failure_error(
                response_error_message,
                exc,
                retryable_same_contract=True,
                failed_session=None,
            ) from exc
        yield format_sse_event(
            response_failed_event(routed_error_code, response_error_message, response_id=get_request_id()),
        )
        return
    except aiohttp.ClientError as exc:
        error_code = process_network_error_code(
            exc,
            fallback="upstream_unavailable",
            include_permanent_dns=not is_proxy_endpoint_failure(exc),
        )
        error_message = _codex_route_transport_error_message(
            route=route,
            route_trace=route_trace,
            operation="stream",
            exc=exc,
        )
        response_error_message = cast(str, error_message)
        retryable_same_contract = transport == "http" and is_pre_dispatch_connection_failure(exc)
        failure_phase = "connect" if retryable_same_contract else "upstream"
        failure_detail = "transport_error"
        failure_exception_type = type(exc).__name__
        # Direct HTTP streams and direct upstream WebSockets both use this
        # leased session. Transport decides replay safety above, not whether
        # the concrete failed shared generation must be retired.
        if error_code == PROCESS_NETWORK_UNAVAILABLE_CODE:
            raise _process_network_failure_error(
                response_error_message,
                exc,
                retryable_same_contract=retryable_same_contract,
                failed_session=client_session,
            ) from exc
        yield format_sse_event(
            response_failed_event(
                error_code or "upstream_unavailable", response_error_message, response_id=get_request_id()
            ),
        )
        return
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError as exc:
        if isinstance(exc, aiohttp.ClientError):
            error_code = "upstream_unavailable"
            error_message = (
                _codex_route_transport_error_message(
                    route=route,
                    route_trace=route_trace,
                    operation="stream",
                    exc=exc,
                )
                if route is not None
                else str(exc) or "Request to upstream timed out"
            )
            response_error_message = cast(str, error_message)
            failure_phase = "upstream"
            failure_detail = "transport_error"
            failure_exception_type = type(exc).__name__
            retryable_same_contract = is_pre_dispatch_connection_failure(exc)
            yield format_sse_event(
                response_failed_event("upstream_unavailable", response_error_message, response_id=get_request_id()),
            )
            return
        now = time.monotonic()
        idle_elapsed_seconds = max(0.0, now - last_stream_activity_at) if last_stream_activity_at is not None else None
        if (
            idle_elapsed_seconds is not None
            and effective_idle_timeout <= (request_total_timeout or effective_idle_timeout)
            and idle_elapsed_seconds >= effective_idle_timeout
        ):
            error_code = "stream_idle_timeout"
            error_message = "Upstream stream idle timeout"
            failure_phase = "upstream"
            failure_detail = "stream_idle_timeout"
            failure_exception_type = type(exc).__name__
            retryable_same_contract = False
            yield format_sse_event(
                response_failed_event(
                    "stream_idle_timeout",
                    "Upstream stream idle timeout",
                    response_id=get_request_id(),
                ),
            )
            return
        total_elapsed_seconds = max(0.0, now - pre_request_started_at)
        if request_total_timeout is None or total_elapsed_seconds < request_total_timeout:
            error_code = "upstream_unavailable"
            error_message = (
                _codex_route_transport_error_message(
                    route=route,
                    route_trace=route_trace,
                    operation="stream",
                    exc=exc,
                )
                if route is not None
                else str(exc) or "Request to upstream timed out"
            )
            response_error_message = cast(str, error_message)
            failure_phase = "upstream"
            failure_detail = "transport_error"
            failure_exception_type = type(exc).__name__
            retryable_same_contract = False
            yield format_sse_event(
                response_failed_event(
                    "upstream_unavailable",
                    response_error_message,
                    response_id=get_request_id(),
                ),
            )
            return
        error_code = "upstream_request_timeout"
        error_message = "Proxy request budget exhausted"
        failure_phase = "upstream"
        failure_detail = "request_timeout"
        failure_exception_type = type(exc).__name__
        retryable_same_contract = False
        yield format_sse_event(
            response_failed_event(
                "upstream_request_timeout",
                "Proxy request budget exhausted",
                response_id=get_request_id(),
            ),
        )
        return
    except GeneratorExit:
        error_code = "client_disconnected"
        error_message = "Downstream client disconnected before response.completed"
        failure_phase = "downstream"
        failure_detail = "client_disconnected_before_terminal_event"
        failure_exception_type = "GeneratorExit"
        retryable_same_contract = False
        raise
    except OSError as exc:
        error_code = process_network_error_code(
            exc,
            fallback="upstream_unavailable",
            include_permanent_dns=not is_proxy_endpoint_failure(exc),
        )
        error_message = _codex_route_transport_error_message(
            route=route,
            route_trace=route_trace,
            operation="stream",
            exc=exc,
        )
        response_error_message = cast(str, error_message)
        retryable_same_contract = transport == "http" and is_pre_dispatch_connection_failure(exc)
        failure_phase = "connect" if retryable_same_contract else "upstream"
        failure_detail = "transport_error"
        failure_exception_type = type(exc).__name__
        if error_code == PROCESS_NETWORK_UNAVAILABLE_CODE:
            raise _process_network_failure_error(
                response_error_message,
                exc,
                retryable_same_contract=retryable_same_contract,
                failed_session=client_session,
            ) from exc
        yield format_sse_event(
            response_failed_event(
                error_code or "upstream_unavailable", response_error_message, response_id=get_request_id()
            ),
        )
        return
    except Exception as exc:
        error_code = "upstream_error"
        error_message = _codex_route_transport_error_message(
            route=route,
            route_trace=route_trace,
            operation="stream",
            exc=exc,
        )
        response_error_message = cast(str, error_message)
        yield format_sse_event(
            response_failed_event("upstream_error", response_error_message, response_id=get_request_id())
        )
        return
    else:
        if not seen_terminal:
            error_code = "stream_incomplete"
            error_message = "Upstream closed stream without completion"
            yield format_sse_event(
                response_failed_event(
                    "stream_incomplete",
                    "Upstream closed stream without completion",
                    response_id=get_request_id(),
                ),
            )
    finally:
        _maybe_log_upstream_request_complete(
            kind="responses",
            url=url,
            headers=upstream_headers,
            method=method,
            started_at=started_at,
            status_code=status_code,
            error_code=error_code,
            error_message=error_message,
            failure_phase=failure_phase,
            failure_detail=failure_detail,
            failure_exception_type=failure_exception_type,
            retryable_same_contract=retryable_same_contract,
        )


def push_stream_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[
    float | None,
    float | None,
    float | None,
]:
    previous = (
        _STREAM_CONNECT_TIMEOUT_OVERRIDE.get(),
        _STREAM_IDLE_TIMEOUT_OVERRIDE.get(),
        _STREAM_TOTAL_TIMEOUT_OVERRIDE.get(),
    )
    _STREAM_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds)
    _STREAM_IDLE_TIMEOUT_OVERRIDE.set(idle_timeout_seconds)
    _STREAM_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds)
    return previous


def pop_stream_timeout_overrides(
    tokens: tuple[
        float | None,
        float | None,
        float | None,
    ],
) -> None:
    connect_timeout, idle_timeout, total_timeout = tokens
    _STREAM_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout)
    _STREAM_IDLE_TIMEOUT_OVERRIDE.set(idle_timeout)
    _STREAM_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout)


@contextlib.contextmanager
def override_stream_timeouts(
    *,
    connect_timeout_seconds: float | None = None,
    idle_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
):
    tokens = push_stream_timeout_overrides(
        connect_timeout_seconds=connect_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        total_timeout_seconds=total_timeout_seconds,
    )
    try:
        yield
    finally:
        pop_stream_timeout_overrides(tokens)


def push_compact_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[contextvars.Token[float | None], contextvars.Token[float | None]]:
    return (
        _COMPACT_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds),
        _COMPACT_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds),
    )


def pop_compact_timeout_overrides(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[float | None]],
) -> None:
    connect_token, total_token = tokens
    _COMPACT_CONNECT_TIMEOUT_OVERRIDE.reset(connect_token)
    _COMPACT_TOTAL_TIMEOUT_OVERRIDE.reset(total_token)


def push_transcribe_timeout_overrides(
    *,
    connect_timeout_seconds: float | None = None,
    total_timeout_seconds: float | None = None,
) -> tuple[contextvars.Token[float | None], contextvars.Token[float | None]]:
    return (
        _TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE.set(connect_timeout_seconds),
        _TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE.set(total_timeout_seconds),
    )


def pop_transcribe_timeout_overrides(
    tokens: tuple[contextvars.Token[float | None], contextvars.Token[float | None]],
) -> None:
    connect_token, total_token = tokens
    _TRANSCRIBE_CONNECT_TIMEOUT_OVERRIDE.reset(connect_token)
    _TRANSCRIBE_TOTAL_TIMEOUT_OVERRIDE.reset(total_token)


async def compact_responses(
    payload: ResponsesCompactRequest,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    session: aiohttp.ClientSession | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    chatgpt_account_id: str | None = None,
    allow_direct_egress: bool = True,
) -> CompactResponsePayload:
    async with lease_http_session(session) as client_session:
        transport = _CompactCommandTransport(
            payload=payload,
            headers=headers,
            access_token=access_token,
            account_id=account_id,
            session=client_session,
            route=route,
            codex_client=codex_client,
            route_trace=route_trace,
            chatgpt_account_id=chatgpt_account_id,
            allow_direct_egress=allow_direct_egress,
        )
        return await transport.execute()


@dataclass(slots=True)
class _CompactCommandTransport:
    payload: ResponsesCompactRequest
    headers: Mapping[str, str]
    access_token: str
    account_id: str | None
    session: aiohttp.ClientSession
    route: ResolvedUpstreamRoute | None = None
    codex_client: CodexClient | None = None
    route_trace: UpstreamProxyRouteTrace | None = None
    chatgpt_account_id: str | None = None
    allow_direct_egress: bool = False

    async def execute(self) -> CompactResponsePayload:
        settings = get_settings()
        upstream_base = settings.upstream_base_url.rstrip("/")
        url = f"{upstream_base}/codex/responses/compact"
        require_route_or_direct_egress_opt_in(
            route=self.route,
            allow_direct_egress=self.allow_direct_egress,
            operation="responses compact",
        )
        if self.route is None and self.route_trace is not None:
            self.route_trace.record_direct()
        upstream_account_id = self.chatgpt_account_id or self.account_id
        upstream_headers = _build_upstream_headers(
            self.headers,
            self.access_token,
            upstream_account_id,
            accept="application/json",
        )
        pre_request_started_at = time.monotonic()
        compact_timeout_seconds = _effective_compact_total_timeout(settings.upstream_compact_timeout_seconds)
        effective_connect_timeout = _effective_compact_connect_timeout(settings.upstream_connect_timeout_seconds)
        payload_dict = dict(self.payload.to_payload())
        if settings.image_inline_fetch_enabled:
            payload_dict = await _inline_input_image_urls(
                payload_dict,
                _as_image_fetch_session(self.session),
                effective_connect_timeout,
            )
        _finalize_responses_lite_reasoning_context(
            payload_dict,
            responses_lite=_payload_uses_responses_lite(payload_dict),
        )
        _apply_responses_lite_http_header(upstream_headers, payload_dict)
        try:
            validate_compact_input_wire_budget(payload_dict)
        except ClientPayloadError as exc:
            error = openai_error(
                exc.code or "invalid_request_error",
                str(exc),
                error_type=exc.error_type or "invalid_request_error",
            )
            if exc.param is not None:
                error["error"]["param"] = exc.param
            raise ProxyResponseError(400, error) from exc
        now = time.monotonic()
        compact_timeout_seconds = _remaining_total_timeout(
            compact_timeout_seconds,
            pre_request_started_at,
            now,
        )
        effective_connect_timeout = max(
            0.001,
            _remaining_total_timeout(
                effective_connect_timeout,
                pre_request_started_at,
                now,
            )
            or effective_connect_timeout,
        )
        timeout = aiohttp.ClientTimeout(
            total=compact_timeout_seconds,
            sock_connect=effective_connect_timeout,
            sock_read=compact_timeout_seconds,
        )
        started_at = time.monotonic()
        status_code: int | None = None
        error_code: str | None = None
        error_message: str | None = None
        failure_phase: str | None = None
        payload_object: str | None = None
        failure_detail: str | None = None
        failure_exception_type: str | None = None
        retryable_same_contract: bool | None = None
        _maybe_log_upstream_request_start(
            kind="responses_compact",
            url=url,
            headers=upstream_headers,
            method="POST",
            payload_summary=_summarize_json_payload(payload_dict),
            payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
            if "upstream_payload" in settings.trace_channels
            else None,
        )
        archive_json(
            direction="codex_to_server",
            kind="compact",
            transport="http",
            payload=payload_dict,
            account_id=self.account_id,
            method="POST",
            url=url,
            headers=upstream_headers,
        )
        try:
            if self.route is not None:
                owns_codex_client = self.codex_client is None
                active_codex_client = self.codex_client or CodexClient(create_codex_session())
                request_kwargs: dict[str, Any] = {"json": payload_dict, "headers": upstream_headers}
                if compact_timeout_seconds is not None:
                    request_kwargs["timeout"] = compact_timeout_seconds
                try:
                    request_with_metadata = getattr(active_codex_client, "request_with_route_metadata", None)
                    if callable(request_with_metadata):
                        result = await request_with_metadata("POST", url, route=self.route, **request_kwargs)
                        resp = result.response
                        if self.route_trace is not None:
                            self.route_trace.record(route=result.route, fallback_used=result.fallback_used)
                    else:
                        resp = await active_codex_client.request("POST", url, route=self.route, **request_kwargs)
                        if self.route_trace is not None:
                            self.route_trace.record(route=self.route, fallback_used=False)
                finally:
                    if owns_codex_client:
                        await active_codex_client.close()
                status_code = _codex_response_status(resp)
                if status_code >= 400:
                    error_payload = await _codex_error_payload_from_response(resp)
                    archive_json(
                        direction="server_to_codex",
                        kind="compact",
                        transport="http",
                        payload=error_payload,
                        account_id=self.account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=upstream_headers,
                    )
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    failure_phase = "status"
                    failure_detail = error_message
                    retryable_same_contract = False
                    raise ProxyResponseError(
                        status_code,
                        error_payload,
                        failure_phase=failure_phase,
                        retryable_same_contract=retryable_same_contract,
                        failure_detail=failure_detail,
                        upstream_status_code=status_code,
                    )
                try:
                    data = await _codex_response_json(resp)
                except Exception as exc:
                    error_code = "upstream_error"
                    error_message = "Invalid JSON from upstream"
                    failure_phase = "parse"
                    failure_detail = str(exc) or error_message
                    failure_exception_type = type(exc).__name__
                    raise ProxyResponseError(
                        502,
                        openai_error("upstream_error", "Invalid JSON from upstream"),
                        failure_phase=failure_phase,
                        failure_detail=failure_detail,
                        failure_exception_type=failure_exception_type,
                        upstream_status_code=status_code,
                    ) from exc
                parsed = parse_compact_response_payload(data)
                archive_json(
                    direction="server_to_codex",
                    kind="compact",
                    transport="http",
                    payload=data,
                    account_id=self.account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=upstream_headers,
                )
                if parsed:
                    payload_object = parsed.object
                    return parsed
                error_code = "upstream_error"
                error_message = "Unexpected upstream payload"
                failure_phase = "parse"
                failure_detail = f"payload_type={type(data).__name__}"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Unexpected upstream payload"),
                    failure_phase=failure_phase,
                    failure_detail=failure_detail,
                    upstream_status_code=status_code,
                )
            async with _service_circuit_breaker_context(
                self.session.post(
                    url,
                    json=payload_dict,
                    headers=upstream_headers,
                    timeout=timeout,
                ),
                settings=settings,
                account_id=self.account_id,
            ) as resp:
                status_code = resp.status
                if resp.status >= 400:
                    error_payload = await _error_payload_from_response(resp)
                    archive_json(
                        direction="server_to_codex",
                        kind="compact",
                        transport="http",
                        payload=error_payload,
                        account_id=self.account_id,
                        method="POST",
                        url=url,
                        status_code=status_code,
                        headers=upstream_headers,
                    )
                    error_code, error_message = _error_details_from_envelope(error_payload)
                    failure_phase = "status"
                    failure_detail = error_message
                    retryable_same_contract = False
                    raise ProxyResponseError(
                        resp.status,
                        error_payload,
                        failure_phase=failure_phase,
                        retryable_same_contract=retryable_same_contract,
                        failure_detail=failure_detail,
                        upstream_status_code=resp.status,
                    )
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
                    message = str(exc) or "Request to upstream timed out"
                    error_code = process_network_error_code(
                        exc,
                        fallback="upstream_unavailable",
                        include_permanent_dns=not is_proxy_endpoint_failure(exc),
                    )
                    error_message = message
                    failure_phase = "body_read"
                    failure_detail = message
                    failure_exception_type = type(exc).__name__
                    retryable_same_contract = False
                    raise ProxyResponseError(
                        502,
                        openai_error(error_code, message),
                        failure_phase=failure_phase,
                        retryable_same_contract=retryable_same_contract,
                        failure_detail=failure_detail,
                        failure_exception_type=failure_exception_type,
                        upstream_status_code=resp.status,
                        failed_session=_failed_shared_session_for_process_network_error(error_code, self.session),
                    ) from exc
                except Exception as exc:
                    error_code = "upstream_error"
                    error_message = "Invalid JSON from upstream"
                    failure_phase = "parse"
                    failure_detail = str(exc) or error_message
                    failure_exception_type = type(exc).__name__
                    raise ProxyResponseError(
                        502,
                        openai_error("upstream_error", "Invalid JSON from upstream"),
                        failure_phase=failure_phase,
                        failure_detail=failure_detail,
                        failure_exception_type=failure_exception_type,
                        upstream_status_code=resp.status,
                    ) from exc
                parsed = parse_compact_response_payload(data)
                archive_json(
                    direction="server_to_codex",
                    kind="compact",
                    transport="http",
                    payload=data,
                    account_id=self.account_id,
                    method="POST",
                    url=url,
                    status_code=status_code,
                    headers=upstream_headers,
                )
                if parsed:
                    payload_object = parsed.object
                    return parsed
                error_code = "upstream_error"
                error_message = "Unexpected upstream payload"
                failure_phase = "parse"
                failure_detail = f"payload_type={type(data).__name__}"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Unexpected upstream payload"),
                    failure_phase=failure_phase,
                    failure_detail=failure_detail,
                    upstream_status_code=resp.status,
                )
        except ProxyResponseError as exc:
            if error_code is None and error_message is None:
                error_code, error_message = _error_details_from_envelope(exc.payload)
            failure_phase = failure_phase or exc.failure_phase
            failure_detail = failure_detail or exc.failure_detail
            failure_exception_type = failure_exception_type or exc.failure_exception_type
            if retryable_same_contract is None:
                retryable_same_contract = exc.retryable_same_contract
            raise
        except CircuitBreakerOpenError as exc:
            error_code = "upstream_unavailable"
            error_message = "Upstream circuit breaker is open"
            failure_phase = "connect"
            failure_detail = str(exc)
            failure_exception_type = type(exc).__name__
            retryable_same_contract = True
            raise ProxyResponseError(
                503,
                openai_error("upstream_unavailable", error_message),
                failure_phase=failure_phase,
                retryable_same_contract=retryable_same_contract,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
            ) from exc
        except CodexTransportError as exc:
            error_code = exc.error_code or "upstream_unavailable"
            error_message = _codex_route_transport_error_message(
                route=self.route,
                route_trace=self.route_trace,
                operation="compact",
                exc=exc,
            )
            failure_phase = exc.failure_phase or ("connect" if exc.retryable_same_contract else "upstream")
            failure_detail = "transport_error"
            failure_exception_type = type(exc).__name__
            retryable_same_contract = exc.retryable_same_contract
            raise ProxyResponseError(
                502,
                openai_error(error_code, error_message),
                failure_phase=failure_phase,
                retryable_same_contract=retryable_same_contract,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                failed_session=None,
            ) from exc
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            message = str(exc) or "Request to upstream timed out"
            error_code = process_network_error_code(
                exc,
                fallback="upstream_unavailable",
                include_permanent_dns=not is_proxy_endpoint_failure(exc),
            )
            error_message = message
            retryable_same_contract = is_pre_dispatch_connection_failure(exc)
            failure_phase = "connect" if retryable_same_contract else "request"
            failure_detail = message
            failure_exception_type = type(exc).__name__
            raise ProxyResponseError(
                502,
                openai_error(error_code, message),
                failure_phase=failure_phase,
                retryable_same_contract=retryable_same_contract,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                failed_session=_failed_shared_session_for_process_network_error(error_code, self.session),
            ) from exc
        except OSError as exc:
            message = str(exc) or "Request to upstream failed"
            error_code = process_network_error_code(
                exc,
                fallback="upstream_unavailable",
                include_permanent_dns=not is_proxy_endpoint_failure(exc),
            )
            error_message = message
            failure_phase = "request"
            failure_detail = message
            failure_exception_type = type(exc).__name__
            retryable_same_contract = False
            raise ProxyResponseError(
                502,
                openai_error(error_code, message),
                failure_phase=failure_phase,
                retryable_same_contract=False,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                failed_session=_failed_shared_session_for_process_network_error(error_code, self.session),
            ) from exc
        except Exception as exc:
            if self.route is None:
                raise
            message = str(exc) or "Request to upstream failed before response"
            error_code = "upstream_unavailable"
            error_message = message
            failure_phase = "request"
            failure_detail = message
            failure_exception_type = type(exc).__name__
            retryable_same_contract = False
            raise ProxyResponseError(
                502,
                openai_error("upstream_unavailable", message),
                failure_phase=failure_phase,
                retryable_same_contract=False,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
            ) from exc
        finally:
            _maybe_log_upstream_request_complete(
                kind="responses_compact",
                url=url,
                headers=upstream_headers,
                method="POST",
                started_at=started_at,
                status_code=status_code,
                error_code=error_code,
                error_message=error_message,
                failure_phase=failure_phase,
                payload_object=payload_object,
                failure_detail=failure_detail,
                failure_exception_type=failure_exception_type,
                retryable_same_contract=retryable_same_contract,
            )


def _codex_response_status(response: Any) -> int:
    value = getattr(response, "status_code", getattr(response, "status", None))
    if value is None:
        return 0
    return int(value)


async def _codex_response_json(response: Any) -> Any:
    json_method = getattr(response, "json", None)
    if callable(json_method):
        result = json_method()
        if asyncio.iscoroutine(result):
            return await result
        return result
    text = await _codex_response_text(response)
    return json.loads(text)


async def _codex_response_text(response: Any) -> str:
    text_value = getattr(response, "text", None)
    if isinstance(text_value, str):
        return text_value
    content = await _codex_response_body(response)
    return content.decode("utf-8", errors="replace")


async def _codex_response_body(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if isinstance(content, str):
        return content.encode()
    acontent = getattr(response, "acontent", None)
    if callable(acontent):
        result = acontent()
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode()
    read = getattr(response, "read", None)
    if callable(read):
        result = read()
        if asyncio.iscoroutine(result):
            result = await result
        if isinstance(result, bytes):
            return result
        if isinstance(result, str):
            return result.encode()
    return b""


def _codex_response_headers(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", {}) or {}
    return {str(key): str(value) for key, value in headers.items()}


async def _codex_error_payload_from_response(response: Any) -> OpenAIErrorEnvelope:
    body = await _codex_response_body(response)
    status = _codex_response_status(response)
    try:
        data: object | None = json.loads(body)
        text = None
    except Exception:
        data = None
        text = body.decode("utf-8", errors="replace")
    if is_json_mapping(data):
        payload_data = cast(dict[str, JsonValue], data)
        error = parse_error_payload(payload_data)
        if error:
            return {"error": _openai_error_detail(error)}
        message = _extract_upstream_message(payload_data)
        if message:
            return openai_error("upstream_error", message)
    message = (text or "").strip() or f"Upstream error: HTTP {status}"
    return openai_error("upstream_error", message)


async def thread_goal_request(
    operation: str,
    payload: Mapping[str, JsonValue],
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    *,
    method: str = "POST",
    timeout_seconds: float | None = None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = True,
) -> dict[str, JsonValue]:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/codex/thread/goal/{operation}"
    upstream_headers = _build_upstream_headers(headers, access_token, account_id, accept="application/json")
    request_method = method.upper()
    total_timeout = (
        max(0.001, timeout_seconds)
        if timeout_seconds is not None
        else _effective_stream_timeout(settings.proxy_request_budget_seconds, "total")
    )
    connect_timeout = min(
        _effective_stream_timeout(settings.upstream_connect_timeout_seconds, "connect"),
        total_timeout,
    )
    timeout = aiohttp.ClientTimeout(
        total=total_timeout,
        sock_connect=connect_timeout,
        sock_read=total_timeout,
    )
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation=f"thread goal {operation}",
    )
    if route is None and route_trace is not None:
        route_trace.record_direct()
    if route is not None:
        lease = None
        client_session = None
    elif session is None:
        lease = await acquire_http_client()
        client_session = lease.client.session
    else:
        lease = None
        client_session = session
    started_at = time.monotonic()
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    payload_dict = dict(payload)
    _maybe_log_upstream_request_start(
        kind=f"thread_goal_{operation}",
        url=url,
        headers=upstream_headers,
        method=request_method,
        payload_summary=_summarize_json_payload(payload_dict),
        payload_json=json.dumps(payload_dict, ensure_ascii=True, separators=(",", ":"))
        if "upstream_payload" in settings.trace_channels
        else None,
    )
    try:
        request_kwargs: dict[str, Any] = {
            "headers": upstream_headers,
            "timeout": total_timeout if route is not None else timeout,
        }
        if request_method == "GET":
            request_kwargs["params"] = {key: str(value) for key, value in payload_dict.items() if value is not None}
        else:
            request_kwargs["json"] = payload_dict
        if route is not None:
            owns_codex_client = codex_client is None
            active_codex_client = codex_client or CodexClient(create_codex_session())
            try:
                request_with_metadata = getattr(active_codex_client, "request_with_route_metadata", None)
                if callable(request_with_metadata):
                    result = await request_with_metadata(request_method, url, route=route, **request_kwargs)
                    response = result.response
                    if route_trace is not None:
                        route_trace.record(route=result.route, fallback_used=result.fallback_used)
                else:
                    response = await active_codex_client.request(request_method, url, route=route, **request_kwargs)
                    if route_trace is not None:
                        route_trace.record(route=route, fallback_used=False)
            finally:
                if owns_codex_client:
                    close = getattr(active_codex_client, "close", None)
                    if callable(close):
                        await close()
            status_code = _codex_response_status(response)
            if status_code >= 400:
                error_payload = await _codex_error_payload_from_response(response)
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(status_code, error_payload)
            try:
                data = await _codex_response_json(response)
            except Exception as exc:
                error_code = "upstream_error"
                error_message = "Invalid JSON from upstream"
                raise ProxyResponseError(502, openai_error("upstream_error", "Invalid JSON from upstream")) from exc
            if isinstance(data, dict):
                return cast(dict[str, JsonValue], data)
            error_code = "upstream_error"
            error_message = "Unexpected upstream payload"
            raise ProxyResponseError(502, openai_error("upstream_error", "Unexpected upstream payload"))
        assert client_session is not None
        async with _service_circuit_breaker_context(
            client_session.request(request_method, url, **request_kwargs),
            settings=settings,
            account_id=account_id,
        ) as resp:
            status_code = resp.status
            if resp.status >= 400:
                try:
                    error_payload = await _error_payload_from_response(resp)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    message = str(exc) or "Request to upstream timed out"
                    error_code = "upstream_unavailable"
                    error_message = message
                    raise ProxyResponseError(
                        resp.status,
                        openai_error("upstream_unavailable", message),
                        failure_phase="status",
                    ) from exc
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(resp.status, error_payload, failure_phase="status")
            try:
                data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                message = str(exc) or "Request to upstream timed out"
                error_code = "upstream_unavailable"
                error_message = message
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", message),
                    failure_phase="body_read",
                ) from exc
            except Exception as exc:
                error_code = "upstream_error"
                error_message = "Invalid JSON from upstream"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Invalid JSON from upstream"),
                    failure_phase="parse",
                ) from exc
            if isinstance(data, dict):
                return cast(dict[str, JsonValue], data)
            error_code = "upstream_error"
            error_message = "Unexpected upstream payload"
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "Unexpected upstream payload"),
                failure_phase="parse",
            )
    except ProxyResponseError as exc:
        if error_code is None and error_message is None:
            error_code, error_message = _error_details_from_envelope(exc.payload)
        raise
    except CircuitBreakerOpenError as exc:
        error_code = "upstream_unavailable"
        error_message = "Upstream circuit breaker is open"
        raise ProxyResponseError(
            503,
            openai_error("upstream_unavailable", error_message),
            failure_phase="connect",
        ) from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to upstream timed out"
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
        ) from exc
    except Exception as exc:
        if route is None:
            raise
        message = str(exc) or "Request to upstream failed before response"
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
        ) from exc
    finally:
        try:
            _maybe_log_upstream_request_complete(
                kind=f"thread_goal_{operation}",
                url=url,
                headers=upstream_headers,
                method=request_method,
                started_at=started_at,
                status_code=status_code,
                error_code=error_code,
                error_message=error_message,
            )
        finally:
            if lease is not None:
                await lease.close()


async def codex_control_request(
    path: str,
    *,
    method: str,
    payload: bytes | None,
    query_params: Mapping[str, str] | Sequence[tuple[str, str]],
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    timeout_seconds: float | None = None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = True,
) -> CodexControlResponse:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    normalized_path = path.strip("/")
    upstream_path = normalized_path if normalized_path.startswith("wham/") else f"codex/{normalized_path}"
    url = f"{upstream_base}/{upstream_path}"
    request_method = method.upper()
    upstream_headers = _build_upstream_headers(headers, access_token, account_id, accept=headers.get("accept", "*/*"))
    content_type = next((value for key, value in headers.items() if key.lower() == "content-type"), None)
    if content_type:
        upstream_headers["Content-Type"] = content_type
    elif payload is None:
        upstream_headers.pop("Content-Type", None)
    total_timeout = (
        max(0.001, timeout_seconds)
        if timeout_seconds is not None
        else _effective_stream_timeout(settings.proxy_request_budget_seconds, "total")
    )
    connect_timeout = min(
        _effective_stream_timeout(settings.upstream_connect_timeout_seconds, "connect"),
        total_timeout,
    )
    timeout = aiohttp.ClientTimeout(
        total=total_timeout,
        sock_connect=connect_timeout,
        sock_read=total_timeout,
    )
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation=f"Codex control {normalized_path}",
    )
    if route is None and route_trace is not None:
        route_trace.record_direct()
    if route is not None:
        lease = None
        client_session = None
    elif session is None:
        lease = await acquire_http_client()
        client_session = lease.client.session
    else:
        lease = None
        client_session = session
    started_at = time.monotonic()
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    payload_summary: dict[str, JsonValue] | None = None
    if payload and content_type and "json" in content_type.lower():
        with contextlib.suppress(Exception):
            decoded = json.loads(payload)
            if isinstance(decoded, dict):
                payload_summary = cast(dict[str, JsonValue], decoded)
    _maybe_log_upstream_request_start(
        kind=f"codex_control_{normalized_path.replace('/', '_')}",
        url=url,
        headers=upstream_headers,
        method=request_method,
        payload_summary=_summarize_json_payload(payload_summary or {}),
        payload_json=payload.decode("utf-8", errors="replace")
        if payload is not None and "upstream_payload" in settings.trace_channels
        else None,
    )
    try:
        if route is not None:
            owns_codex_client = codex_client is None
            active_codex_client = codex_client or CodexClient(create_codex_session())
            try:
                request_kwargs = {
                    "route": route,
                    "params": query_params,
                    "data": payload,
                    "headers": upstream_headers,
                    "timeout": total_timeout,
                }
                request_with_metadata = getattr(active_codex_client, "request_with_route_metadata", None)
                if callable(request_with_metadata):
                    result = await request_with_metadata(request_method, url, **request_kwargs)
                    response = result.response
                    if route_trace is not None:
                        route_trace.record(route=result.route, fallback_used=result.fallback_used)
                else:
                    response = await active_codex_client.request(request_method, url, **request_kwargs)
                    if route_trace is not None:
                        route_trace.record(route=route, fallback_used=False)
            finally:
                if owns_codex_client:
                    close = getattr(active_codex_client, "close", None)
                    if callable(close):
                        await close()
            status_code = _codex_response_status(response)
            body = await _codex_response_body(response)
            if status_code >= 400:
                error_payload = await _codex_error_payload_from_response(response)
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(status_code, error_payload)
            return CodexControlResponse(
                status_code=status_code,
                body=body,
                headers=_codex_response_headers(response),
            )
        assert client_session is not None
        async with _service_circuit_breaker_context(
            client_session.request(
                request_method,
                url,
                params=query_params,
                data=payload,
                headers=upstream_headers,
                timeout=timeout,
            ),
            settings=settings,
            account_id=account_id,
        ) as resp:
            status_code = resp.status
            try:
                body = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                message = str(exc) or "Request to upstream timed out"
                error_code = "upstream_unavailable"
                error_message = message
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", message),
                    failure_phase="body_read",
                ) from exc
            if resp.status >= 400:
                error_payload = await _error_payload_from_raw_body(resp, body)
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(resp.status, error_payload, failure_phase="status")
            return CodexControlResponse(
                status_code=resp.status,
                body=body,
                headers={key: value for key, value in resp.headers.items()},
            )
    except ProxyResponseError as exc:
        if error_code is None and error_message is None:
            error_code, error_message = _error_details_from_envelope(exc.payload)
        raise
    except CircuitBreakerOpenError as exc:
        error_code = "upstream_unavailable"
        error_message = "Upstream circuit breaker is open"
        raise ProxyResponseError(
            503,
            openai_error("upstream_unavailable", error_message),
            failure_phase="connect",
        ) from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to upstream timed out"
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
        ) from exc
    except Exception as exc:
        if route is None:
            raise
        message = str(exc) or "Request to upstream failed before response"
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
        ) from exc
    finally:
        try:
            _maybe_log_upstream_request_complete(
                kind=f"codex_control_{normalized_path.replace('/', '_')}",
                url=url,
                headers=upstream_headers,
                method=request_method,
                started_at=started_at,
                status_code=status_code,
                error_code=error_code,
                error_message=error_message,
            )
        finally:
            if lease is not None:
                await lease.close()


async def transcribe_audio(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str | None,
    prompt: str | None,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    session: aiohttp.ClientSession | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = True,
) -> dict[str, JsonValue]:
    async with lease_http_session(session) as client_session:
        return await _transcribe_audio_with_session(
            audio_bytes,
            filename=filename,
            content_type=content_type,
            prompt=prompt,
            headers=headers,
            access_token=access_token,
            account_id=account_id,
            base_url=base_url,
            session=client_session,
            route=route,
            codex_client=codex_client,
            route_trace=route_trace,
            allow_direct_egress=allow_direct_egress,
        )


async def _transcribe_audio_with_session(
    audio_bytes: bytes,
    *,
    filename: str,
    content_type: str | None,
    prompt: str | None,
    headers: Mapping[str, str],
    access_token: str,
    account_id: str | None,
    session: aiohttp.ClientSession,
    base_url: str | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    route_trace: UpstreamProxyRouteTrace | None = None,
    allow_direct_egress: bool = False,
) -> dict[str, JsonValue]:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    url = f"{upstream_base}/transcribe"
    upstream_headers = _build_upstream_transcribe_headers(
        headers,
        access_token,
        account_id,
    )

    effective_total_timeout = _effective_transcribe_total_timeout(
        settings.transcription_request_budget_seconds,
    )
    effective_connect_timeout = _effective_transcribe_connect_timeout(settings.upstream_connect_timeout_seconds)
    timeout = aiohttp.ClientTimeout(
        total=effective_total_timeout,
        sock_connect=effective_connect_timeout,
        sock_read=effective_total_timeout,
    )
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="audio transcription",
    )
    if route is None and route_trace is not None:
        route_trace.record_direct()

    normalized_filename = filename.strip() if filename else ""
    if not normalized_filename:
        normalized_filename = "audio.wav"
    normalized_content_type = content_type.strip() if content_type else ""
    if not normalized_content_type:
        normalized_content_type = "application/octet-stream"

    form = aiohttp.FormData()
    form.add_field(
        "file",
        audio_bytes,
        filename=normalized_filename,
        content_type=normalized_content_type,
    )
    if prompt is not None:
        form.add_field("prompt", prompt)

    client_session = session
    started_at = time.monotonic()
    status_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata = _summarize_transcription_payload(
        filename=normalized_filename,
        content_type=normalized_content_type,
        prompt=prompt,
        audio_bytes=audio_bytes,
    )
    _maybe_log_upstream_request_start(
        kind="transcribe",
        url=url,
        headers=upstream_headers,
        method="POST",
        payload_summary=json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        payload_json=json.dumps(metadata, ensure_ascii=True, separators=(",", ":"))
        if "upstream_payload" in settings.trace_channels
        else None,
    )
    try:
        if route is not None:
            owns_codex_client = codex_client is None
            active_codex_client = codex_client or CodexClient(create_codex_session())
            data_fields: dict[str, str] = {}
            if prompt is not None:
                data_fields["prompt"] = prompt
            try:
                request_with_metadata = getattr(active_codex_client, "request_with_route_metadata", None)
                request_kwargs: dict[str, Any] = {
                    "files": {
                        "file": (
                            normalized_filename,
                            audio_bytes,
                            normalized_content_type,
                        )
                    },
                    "data": data_fields,
                    "headers": upstream_headers,
                    "timeout": effective_total_timeout,
                }
                if callable(request_with_metadata):
                    result = await request_with_metadata("POST", url, route=route, **request_kwargs)
                    response = result.response
                    if route_trace is not None:
                        route_trace.record(route=result.route, fallback_used=result.fallback_used)
                else:
                    response = await active_codex_client.request("POST", url, route=route, **request_kwargs)
                    if route_trace is not None:
                        route_trace.record(route=route, fallback_used=False)
            except CodexTransportError as exc:
                error_code = "upstream_unavailable"
                error_message = _codex_route_transport_error_message(
                    route=route,
                    route_trace=route_trace,
                    operation="transcribe",
                    exc=exc,
                )
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", error_message),
                ) from exc
            finally:
                if owns_codex_client:
                    await active_codex_client.close()
            status_code = _codex_response_status(response)
            if status_code >= 400:
                error_payload = await _codex_error_payload_from_response(response)
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(status_code, error_payload)
            try:
                data = await _codex_response_json(response)
            except Exception as exc:
                error_code = "upstream_error"
                error_message = "Invalid JSON from upstream"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Invalid JSON from upstream"),
                ) from exc
            if isinstance(data, dict):
                return cast(dict[str, JsonValue], data)
            error_code = "upstream_error"
            error_message = "Unexpected upstream payload"
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "Unexpected upstream payload"),
            )
        async with _service_circuit_breaker_context(
            client_session.post(
                url,
                data=form,
                headers=upstream_headers,
                timeout=timeout,
            ),
            settings=settings,
            account_id=account_id,
        ) as resp:
            status_code = resp.status
            if resp.status >= 400:
                try:
                    error_payload = await _error_payload_from_response(resp)
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    message = str(exc) or "Request to upstream timed out"
                    error_code = "upstream_unavailable"
                    error_message = message
                    raise ProxyResponseError(
                        resp.status,
                        openai_error("upstream_unavailable", message),
                        failure_phase="status",
                    ) from exc
                error_code, error_message = _error_details_from_envelope(error_payload)
                raise ProxyResponseError(resp.status, error_payload, failure_phase="status")
            try:
                data = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                message = str(exc) or "Request to upstream timed out"
                error_code = "upstream_unavailable"
                error_message = message
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_unavailable", message),
                    failure_phase="body_read",
                ) from exc
            except Exception as exc:
                error_code = "upstream_error"
                error_message = "Invalid JSON from upstream"
                raise ProxyResponseError(
                    502,
                    openai_error("upstream_error", "Invalid JSON from upstream"),
                    failure_phase="parse",
                ) from exc
            if isinstance(data, dict):
                return data
            raise ProxyResponseError(
                502,
                openai_error("upstream_error", "Unexpected upstream payload"),
                failure_phase="parse",
            )
    except ProxyResponseError as exc:
        if error_code is None and error_message is None:
            error_code, error_message = _error_details_from_envelope(exc.payload)
        raise
    except CircuitBreakerOpenError as exc:
        error_code = "upstream_unavailable"
        error_message = "Upstream circuit breaker is open"
        raise ProxyResponseError(
            503,
            openai_error("upstream_unavailable", error_message),
            failure_phase="connect",
        ) from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        message = str(exc) or "Request to upstream timed out"
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
        ) from exc
    except Exception as exc:
        if route is None:
            raise
        message = _codex_route_transport_error_message(
            route=route,
            route_trace=route_trace,
            operation="transcribe",
            exc=exc,
        )
        error_code = "upstream_unavailable"
        error_message = message
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
        ) from exc
    finally:
        _maybe_log_upstream_request_complete(
            kind="transcribe",
            url=url,
            headers=upstream_headers,
            method="POST",
            started_at=started_at,
            status_code=status_code,
            error_code=error_code,
            error_message=error_message,
        )
