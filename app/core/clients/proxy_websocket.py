from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import ssl
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, NoReturn, Protocol, Sequence, cast
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import aiohttp
from websockets.asyncio.client import ClientConnection
from websockets.asyncio.client import connect as websocket_connect
from websockets.datastructures import Headers
from websockets.exceptions import (
    ConnectionClosedError,
    ConnectionClosedOK,
    InvalidHandshake,
    InvalidProxy,
    InvalidStatus,
)
from websockets.typing import Origin, Subprotocol

from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    codex_transport_error_message,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.proxy import (
    _CHATGPT_ACCOUNT_ID_HEADER,
    _HOP_BY_HOP_HEADER_NAMES,
    CODEX_INSTALLATION_ID_HEADER,
    ProxyResponseError,
    _is_native_codex_request,
    _is_upstream_edge_challenge,
    _normalize_non_native_upstream_fingerprint,
    filter_inbound_headers,
)
from app.core.config.settings import get_settings
from app.core.conversation_archive import archive_bytes, archive_text
from app.core.errors import OpenAIErrorDetail, OpenAIErrorEnvelope, openai_error
from app.core.openai.models import OpenAIError
from app.core.openai.parsing import parse_error_payload
from app.core.resilience.network_recovery import (
    PROCESS_NETWORK_UNAVAILABLE_CODE,
    process_network_error_code,
    rotate_shared_http_transport,
)
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.proxy_env import resolve_websocket_proxy_from_env
from app.core.utils.request_id import get_request_id

_WEBSOCKET_HOP_BY_HOP_HEADERS = _HOP_BY_HOP_HEADER_NAMES | frozenset(
    {
        "accept-encoding",
        "cookie",
        "sec-websocket-extensions",
        "sec-websocket-key",
        "sec-websocket-protocol",
        "sec-websocket-version",
    }
)
_RESPONSES_WEBSOCKET_BETA_HEADER = "responses_websockets=2026-02-06"
_RESPONSES_WEBSOCKET_INCOMPATIBLE_BETA_HEADERS = frozenset({"responses=experimental"})
_OPENAI_LIVE_BASE_URL = "https://api.openai.com/v1"
_LIVE_CALL_ID_MAX_LENGTH = 256
_LIVE_CALL_ID_RTC_PREFIX = "rtc_"
# Keep the route convertor and normalizer on one grammar: total-length-capped rtc_
# ids using the installed-app character set, or a hyphenated UUID form.
_LIVE_CALL_ID_CHAR_CLASS = r"A-Za-z0-9._~\-"
_LIVE_CALL_UUID_CORE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_LIVE_CALL_ID_RTC_MAX_SUFFIX_LENGTH = _LIVE_CALL_ID_MAX_LENGTH - len(_LIVE_CALL_ID_RTC_PREFIX)
REALTIME_LIVE_CALL_ID_ROUTE_REGEX = (
    rf"(?:{_LIVE_CALL_ID_RTC_PREFIX}[{_LIVE_CALL_ID_CHAR_CLASS}]{{1,{_LIVE_CALL_ID_RTC_MAX_SUFFIX_LENGTH}}}"
    rf"|{_LIVE_CALL_UUID_CORE})"
)
_LIVE_CALL_ID_PATTERN = re.compile(rf"{REALTIME_LIVE_CALL_ID_ROUTE_REGEX}\Z")
UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE = "upstream_websocket_liveness_timeout"
_WEBSOCKETS_KEEPALIVE_TIMEOUT_REASON = "keepalive ping timeout"
_AIOHTTP_HEARTBEAT_TIMEOUT_PREFIX = "No PONG received after "


class RealtimeWebSocketProtocol(StrEnum):
    LIVE_V3 = "live_v3"
    REALTIME_V1_V2 = "realtime_v1_v2"


@dataclass(frozen=True, slots=True)
class _UpstreamWebSocketPolicy:
    operation: str
    include_responses_beta: bool
    archive_payloads: bool
    enable_routed_heartbeat: bool
    retry_handshake_status: bool
    preserve_handshake_status: bool
    credential_safe_connect_errors: bool
    retry_routed_network_errors: bool
    enable_direct_ping_timeout: bool
    preserve_close_semantics: bool


# Responses turns may be silent at the application layer for minutes, but a
# healthy transport still answers ping control frames. Keep both watchdogs on:
# disabling them turns a black-holed VPN route into a multi-hour request stall.
_RESPONSES_WEBSOCKET_POLICY = _UpstreamWebSocketPolicy(
    operation="responses websocket",
    include_responses_beta=True,
    archive_payloads=True,
    enable_routed_heartbeat=True,
    retry_handshake_status=True,
    preserve_handshake_status=False,
    credential_safe_connect_errors=False,
    retry_routed_network_errors=True,
    enable_direct_ping_timeout=True,
    preserve_close_semantics=False,
)
_LIVE_SIDEBAND_WEBSOCKET_POLICY = _UpstreamWebSocketPolicy(
    operation="live websocket",
    include_responses_beta=False,
    archive_payloads=False,
    enable_routed_heartbeat=True,
    retry_handshake_status=False,
    preserve_handshake_status=True,
    credential_safe_connect_errors=True,
    retry_routed_network_errors=False,
    enable_direct_ping_timeout=True,
    preserve_close_semantics=True,
)

# Provenance proving the upstream websocket transport itself failed to come
# up, as opposed to an account-, route-, or configuration-scoped connect
# failure that happens to share the sanitized ``upstream_unavailable``
# envelope. Only the direct upstream open can establish it: a routed open
# proves nothing beyond the health of that one account's proxy endpoint, and
# TLS verification failures and host-wide network loss are conditions that
# switching the request to raw HTTP cannot route around.
UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL = "upstream_websocket_transport_unavailable"


logger = logging.getLogger(__name__)


def normalize_realtime_call_id(value: str) -> str | None:
    normalized = value.strip()
    if not normalized or len(normalized) > _LIVE_CALL_ID_MAX_LENGTH:
        return None
    if _LIVE_CALL_ID_PATTERN.fullmatch(normalized) is None:
        return None
    if normalized.startswith(_LIVE_CALL_ID_RTC_PREFIX):
        return normalized
    return normalized.lower()


def _consume_connection_lost_exception(done: asyncio.Future[Any]) -> None:
    """Retrieve close exceptions before websockets shields the waiter.

    websockets 16 waits on ``connection_lost_waiter`` through
    ``asyncio.shield`` while completing ``ClientConnection.recv``.  A peer
    keepalive/protocol close therefore leaves an exception on the waiter,
    which asyncio reports as an ``exception in shielded future`` even though
    ``recv`` translates it into an ``UpstreamWebSocketMessage``.  Consume it
    at the adapter boundary; ``receive`` still classifies the close normally.
    """
    if done.cancelled():
        return
    try:
        done.exception()
    except asyncio.CancelledError:
        return


@dataclass(slots=True)
class UpstreamWebSocketMessage:
    kind: str
    text: str | None = None
    data: bytes | None = None
    close_code: int | None = None
    close_reason: str | None = None
    error: str | None = None
    error_code: str | None = None


class UpstreamWebSocketTransportError(RuntimeError):
    """Credential-safe post-connect transport failure with stable classification."""

    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _websocket_transport_error_code(exc: BaseException, *, uses_proxy: bool) -> str:
    if _is_websocket_liveness_timeout(exc):
        return UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE
    return process_network_error_code(
        exc,
        fallback="upstream_unavailable",
        include_permanent_dns=not uses_proxy,
    )


def is_account_neutral_websocket_error_code(error_code: str | None) -> bool:
    """Return whether transport provenance rules out an account-health penalty."""

    # These failures occur below the selected account's application protocol.
    # They follow an ambiguous send, so relay owners must fail rather than
    # replay while leaving the account eligible for unrelated requests. Keep
    # the compatibility keepalive code here as long as adapters can emit it.
    return error_code in {
        PROCESS_NETWORK_UNAVAILABLE_CODE,
        UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE,
        "upstream_keepalive_timeout",
    }


def _is_websocket_liveness_timeout(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionClosedError):
        # websockets emits this locally-sent 1011 when its own ping watchdog
        # expires. A peer may acknowledge it, leaving both close frames on the
        # exception; send-first ordering still proves the marker came from our
        # watchdog without trusting a peer that sends the same code and reason.
        return (
            exc.sent is not None
            and int(exc.sent.code) == 1011
            and exc.sent.reason == _WEBSOCKETS_KEEPALIVE_TIMEOUT_REASON
            and (exc.rcvd is None or exc.rcvd_then_sent is False)
        )
    # aiohttp surfaces its heartbeat watchdog through WSMsgType.ERROR with a
    # ServerTimeoutError carrying this library-defined prefix.
    return isinstance(exc, aiohttp.ServerTimeoutError) and str(exc).startswith(_AIOHTTP_HEARTBEAT_TIMEOUT_PREFIX)


def _aiohttp_stored_liveness_exception(websocket: Any) -> Exception | None:
    # When aiohttp's heartbeat expires between receive() calls, no waiter is
    # available for WSMsgType.ERROR. aiohttp stores the timeout instead and the
    # next receive returns CLOSED, so every post-connect path must consult it.
    exception_getter = getattr(websocket, "exception", None)
    if not callable(exception_getter):
        return None
    exception = exception_getter()
    return exception if isinstance(exception, Exception) and _is_websocket_liveness_timeout(exception) else None


def _relay_receive_error_code(error_code: str) -> str | None:
    """Expose account-neutral transport failures across the adapter boundary."""

    # Relay owners map an absent code to their established stream_incomplete
    # contract. Leaking the adapter's generic fallback would bypass that path.
    return error_code if is_account_neutral_websocket_error_code(error_code) else None


def _is_keepalive_timeout_close(exc: ConnectionClosedError) -> bool:
    """Classify peer/proxy heartbeat failures without exposing socket details."""

    # Treat the legacy text marker as trusted only when this endpoint initiated
    # the close. A peer can send the same public code and reason, so peer-first
    # ordering must retain ordinary close/error semantics.
    if exc.sent is None or (exc.rcvd is not None and exc.rcvd_then_sent is not False):
        return False
    reason = _close_reason_from_exception(exc)
    return "keepalive ping timeout" in f"{exc} {reason or ''}".lower()


async def _rotate_after_websocket_network_failure(error_code: str) -> None:
    if error_code != PROCESS_NETWORK_UNAVAILABLE_CODE:
        return
    try:
        await rotate_shared_http_transport(transport="websocket", request_id=get_request_id())
    except Exception:
        # Rotation is best-effort here: never replace the credential-safe
        # socket failure that the owning request must surface.
        logger.warning("Failed to rotate shared HTTP state after websocket network failure", exc_info=True)


async def _raise_websocket_send_error(
    exc: Exception,
    *,
    endpoint_id: str | None = None,
    uses_proxy: bool,
) -> NoReturn:
    error_code = _websocket_transport_error_code(exc, uses_proxy=uses_proxy)
    await _rotate_after_websocket_network_failure(error_code)
    # A send exception does not prove whether the peer received the complete
    # frame. The typed error lets every caller fail closed instead of replaying.
    raise UpstreamWebSocketTransportError(
        codex_transport_error_message("websocket send", endpoint_id, exc),
        error_code=error_code,
    ) from None


class UpstreamWebSocket(Protocol):
    async def send_text(self, text: str) -> None: ...

    async def send_bytes(self, data: bytes) -> None: ...

    async def receive(self) -> UpstreamWebSocketMessage: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...

    def response_header(self, name: str) -> str | None: ...


class WebsocketsUpstreamWebSocket:
    def __init__(
        self,
        connection: ClientConnection,
        *,
        uses_proxy: bool = False,
        preserve_close_semantics: bool = False,
    ) -> None:
        self._connection = connection
        self._uses_proxy = uses_proxy
        self._preserve_close_semantics = preserve_close_semantics
        connection_lost_waiter = getattr(connection, "connection_lost_waiter", None)
        if isinstance(connection_lost_waiter, asyncio.Future):
            connection_lost_waiter.add_done_callback(_consume_connection_lost_exception)

    async def send_text(self, text: str) -> None:
        try:
            await self._connection.send(text)
        except Exception as exc:
            await _raise_websocket_send_error(exc, uses_proxy=self._uses_proxy)

    async def send_bytes(self, data: bytes) -> None:
        try:
            await self._connection.send(data)
        except Exception as exc:
            await _raise_websocket_send_error(exc, uses_proxy=self._uses_proxy)

    async def receive(self) -> UpstreamWebSocketMessage:
        try:
            message = await self._connection.recv()
        except ConnectionClosedOK as exc:
            return UpstreamWebSocketMessage(
                kind="close",
                close_code=_close_code_from_exception(exc),
                close_reason=_close_reason_from_exception(exc),
            )
        except ConnectionClosedError as exc:
            if self._preserve_close_semantics and exc.rcvd is not None:
                return UpstreamWebSocketMessage(
                    kind="close",
                    close_code=_close_code_from_exception(exc),
                    close_reason=_close_reason_from_exception(exc),
                )
            error_code = _websocket_transport_error_code(exc, uses_proxy=self._uses_proxy)
            await _rotate_after_websocket_network_failure(error_code)
            relay_error_code = _relay_receive_error_code(error_code)
            if relay_error_code is None and _is_keepalive_timeout_close(exc):
                # Prefer the stable, provenance-checked watchdog code above.
                # This text fallback preserves compatibility with keepalive
                # failures whose exception shape lacks the local-send marker.
                relay_error_code = "upstream_keepalive_timeout"
            # ConnectionClosedError describes an incomplete close handshake,
            # not generic transport provenance. Let Responses relay owners map
            # it to stream_incomplete while live relays preserve received closes.
            return UpstreamWebSocketMessage(
                kind="error",
                close_code=_close_code_from_exception(exc),
                error=(
                    "Upstream websocket closed without a complete handshake"
                    if self._preserve_close_semantics
                    else str(exc)
                ),
                error_code=relay_error_code,
            )
        except Exception as exc:
            error_code = _websocket_transport_error_code(exc, uses_proxy=self._uses_proxy)
            await _rotate_after_websocket_network_failure(error_code)
            return UpstreamWebSocketMessage(
                kind="error",
                error=codex_transport_error_message("websocket receive", None, exc),
                error_code=_relay_receive_error_code(error_code),
            )

        if isinstance(message, str):
            return UpstreamWebSocketMessage(kind="text", text=message)
        if isinstance(message, bytes):
            return UpstreamWebSocketMessage(kind="binary", data=message)
        return UpstreamWebSocketMessage(kind="error", error=f"Unexpected websocket message type: {type(message)!r}")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._connection.close(code=code, reason=reason)

    def response_header(self, name: str) -> str | None:
        if name.lower() == "sec-websocket-protocol":
            selected = getattr(self._connection, "subprotocol", None)
            if selected is not None:
                return cast(str, selected)
        response = getattr(self._connection, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        value = headers.get(name)
        if value is None:
            return None
        return str(value)


class CodexUpstreamWebSocket:
    def __init__(
        self,
        websocket: Any,
        *,
        context: Any | None = None,
        codex_client: CodexClient | None = None,
        owns_codex_client: bool = False,
        endpoint_id: str | None = None,
        response_headers: Mapping[str, str] | None = None,
    ) -> None:
        self._websocket = websocket
        self._context = context
        self._codex_client = codex_client
        self._owns_codex_client = owns_codex_client
        self._endpoint_id = endpoint_id
        self._response_headers = _normalize_response_headers(response_headers)

    async def send_text(self, text: str) -> None:
        try:
            result = self._websocket.send_str(text)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            classification_exc = _aiohttp_stored_liveness_exception(self._websocket) or exc
            await _raise_websocket_send_error(classification_exc, endpoint_id=self._endpoint_id, uses_proxy=True)

    async def send_bytes(self, data: bytes) -> None:
        try:
            result = self._websocket.send_bytes(data)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            classification_exc = _aiohttp_stored_liveness_exception(self._websocket) or exc
            await _raise_websocket_send_error(classification_exc, endpoint_id=self._endpoint_id, uses_proxy=True)

    async def receive(self) -> UpstreamWebSocketMessage:
        try:
            msg = await self._websocket.receive()
        except Exception as exc:
            classification_exc = _aiohttp_stored_liveness_exception(self._websocket) or exc
            error_code = _websocket_transport_error_code(classification_exc, uses_proxy=True)
            await _rotate_after_websocket_network_failure(error_code)
            return UpstreamWebSocketMessage(
                kind="error",
                error=codex_transport_error_message("websocket receive", self._endpoint_id, classification_exc),
                error_code=_relay_receive_error_code(error_code),
            )
        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED):
            liveness_exception = _aiohttp_stored_liveness_exception(self._websocket)
            if liveness_exception is not None:
                return UpstreamWebSocketMessage(
                    kind="error",
                    close_code=_aiohttp_ws_close_code(self._websocket, msg),
                    error=codex_transport_error_message(
                        "websocket receive",
                        self._endpoint_id,
                        liveness_exception,
                    ),
                    error_code=UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE,
                )
            return UpstreamWebSocketMessage(
                kind="close",
                close_code=_aiohttp_ws_close_code(self._websocket, msg),
                close_reason=_aiohttp_ws_close_reason(msg),
            )
        if msg.type == aiohttp.WSMsgType.ERROR:
            exception = (
                msg.data if isinstance(msg.data, Exception) else _aiohttp_stored_liveness_exception(self._websocket)
            )
            error_code = (
                _websocket_transport_error_code(exception, uses_proxy=True)
                if exception is not None
                else "upstream_unavailable"
            )
            await _rotate_after_websocket_network_failure(error_code)
            return UpstreamWebSocketMessage(
                kind="error",
                error=(
                    codex_transport_error_message("websocket receive", self._endpoint_id, exception)
                    if exception is not None
                    else "Upstream websocket error"
                ),
                error_code=_relay_receive_error_code(error_code),
            )
        if msg.type == aiohttp.WSMsgType.TEXT:
            text = msg.data if isinstance(msg.data, str) else str(msg.data)
            return UpstreamWebSocketMessage(kind="text", text=text)
        if msg.type == aiohttp.WSMsgType.BINARY:
            return UpstreamWebSocketMessage(kind="binary", data=bytes(msg.data) if isinstance(msg.data, bytes) else b"")
        return UpstreamWebSocketMessage(kind="error", error=f"Unexpected ws type: {msg.type!r}")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        try:
            result = self._websocket.close(code=code, message=reason.encode("utf-8"))
            if asyncio.iscoroutine(result):
                await result
        finally:
            try:
                if self._context is not None:
                    await self._context.__aexit__(None, None, None)
            finally:
                # Context and client ownership are independent: a failed
                # websocket exit must not leak the session this wrapper owns.
                if self._owns_codex_client and self._codex_client is not None:
                    await self._codex_client.close()

    def response_header(self, name: str) -> str | None:
        if name.lower() == "sec-websocket-protocol":
            selected = getattr(self._websocket, "protocol", None)
            if selected is not None:
                return cast(str, selected)
        return self._response_headers.get(name.lower())


class ArchivingUpstreamWebSocket:
    def __init__(
        self,
        wrapped: UpstreamWebSocket,
        *,
        url: str,
        headers: dict[str, str],
        account_id: str | None,
        route: ResolvedUpstreamRoute | None = None,
        fallback_used: bool | None = None,
        direct_egress: bool = False,
        archive_payloads: bool = True,
    ) -> None:
        self._wrapped = wrapped
        self._url = url
        self._headers = headers
        self._account_id = account_id
        self._archive_payloads = archive_payloads
        self.upstream_proxy_route_mode = route.mode if route is not None else ("direct" if direct_egress else None)
        self.upstream_proxy_pool_id = route.pool_id if route is not None else None
        self.upstream_proxy_endpoint_id = route.endpoint_id if route is not None else None
        self.upstream_proxy_fallback_used = fallback_used if route is not None else None

    async def send_text(self, text: str) -> None:
        if self._archive_payloads:
            archive_text(
                direction="codex_to_server",
                kind="responses",
                transport="websocket",
                text=text,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "text"},
            )
        await self._wrapped.send_text(text)

    async def send_bytes(self, data: bytes) -> None:
        if self._archive_payloads:
            archive_bytes(
                direction="codex_to_server",
                kind="responses",
                transport="websocket",
                data=data,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "binary"},
            )
        await self._wrapped.send_bytes(data)

    async def receive(self) -> UpstreamWebSocketMessage:
        message = await self._wrapped.receive()
        return message

    def archive_received(self, message: UpstreamWebSocketMessage) -> None:
        if not self._archive_payloads:
            return
        if message.kind == "text" and message.text is not None:
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=message.text,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "text"},
            )
        elif message.kind == "binary" and message.data is not None:
            archive_bytes(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                data=message.data,
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": "binary"},
            )
        else:
            archive_text(
                direction="server_to_codex",
                kind="responses",
                transport="websocket",
                text=message.error or "",
                account_id=self._account_id,
                method="GET",
                url=self._url,
                headers=self._headers,
                extra={"frame_type": message.kind, "close_code": message.close_code},
            )

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._wrapped.close(code=code, reason=reason)

    def response_header(self, name: str) -> str | None:
        return self._wrapped.response_header(name)


def _connection_header_tokens(headers: Mapping[str, str]) -> set[str]:
    tokens: set[str] = set()
    for key, value in headers.items():
        if key.lower() != "connection":
            continue
        tokens.update(token.strip().lower() for token in value.split(",") if token.strip())
    return tokens


def filter_inbound_websocket_headers(headers: Mapping[str, str]) -> dict[str, str]:
    filtered = filter_inbound_headers(headers)
    blocked_header_names = _WEBSOCKET_HOP_BY_HOP_HEADERS | _connection_header_tokens(filtered)
    return {key: value for key, value in filtered.items() if key.lower() not in blocked_header_names}


def _build_upstream_websocket_headers(
    inbound: dict[str, str],
    access_token: str,
    account_id: str | None,
    *,
    include_responses_beta: bool = True,
    normalize_non_native_fingerprint: bool = True,
) -> dict[str, str]:
    headers = filter_inbound_websocket_headers(inbound)
    # ``filter_inbound_websocket_headers`` strips ``x-codex-installation-id`` because it
    # lives in ``IGNORE_INBOUND_HEADERS``. Callers normalize the selected account's
    # canonical installation id onto the inbound headers before connecting (mirroring the
    # HTTP ``/codex/responses`` egress, where ``apply_codex_installation_headers`` runs as
    # the final post-filter step). Re-add it here so the websocket handshake keeps header
    # parity instead of losing the standalone installation header to this second filter.
    installation_id = next(
        (value for key, value in inbound.items() if key.lower() == CODEX_INSTALLATION_ID_HEADER),
        None,
    )
    if installation_id:
        headers[CODEX_INSTALLATION_ID_HEADER] = installation_id
    native = _is_native_codex_request(headers)
    lower_keys = {key.lower() for key in headers}
    if "x-request-id" not in lower_keys and "request-id" not in lower_keys:
        request_id = get_request_id()
        if request_id:
            headers["x-request-id"] = request_id
    # Normalize a non-native client's fingerprint on the client-facing
    # ``/v1/responses`` websocket egress too. This builder is the upstream egress
    # for a direct websocket caller, so without normalization an OpenAI SDK that
    # speaks the responses websocket protocol would reach upstream with its
    # ``OpenAI/Python`` / ``x-openai-client-*`` / ``x-stainless-*`` fingerprint
    # intact and trigger the priority downgrade this change exists to prevent.
    if normalize_non_native_fingerprint and not native:
        _normalize_non_native_upstream_fingerprint(headers)
    headers["Authorization"] = f"Bearer {access_token}"
    if account_id:
        if native:
            headers["chatgpt-account-id"] = account_id
        else:
            headers[_CHATGPT_ACCOUNT_ID_HEADER] = account_id
    if include_responses_beta:
        _ensure_responses_websocket_beta_header(headers)
    return headers


def _build_upstream_live_websocket_headers(
    inbound: dict[str, str],
    access_token: str,
    account_id: str | None,
) -> dict[str, str]:
    headers = _build_upstream_websocket_headers(
        inbound,
        access_token,
        account_id,
        include_responses_beta=False,
        normalize_non_native_fingerprint=False,
    )
    beta_value = _pop_header_case_insensitive(headers, "openai-beta")
    if beta_value:
        retained_tokens: list[str] = []
        for raw_token in beta_value.split(","):
            token = raw_token.strip()
            if not token:
                continue
            normalized_token = token.lower()
            if (
                normalized_token in _RESPONSES_WEBSOCKET_INCOMPATIBLE_BETA_HEADERS
                or normalized_token.partition("=")[0].strip() == "responses_websockets"
            ):
                continue
            retained_tokens.append(token)
        if retained_tokens:
            headers["openai-beta"] = ", ".join(retained_tokens)
    return headers


def _live_websocket_url(
    call_id: str,
    *,
    protocol: RealtimeWebSocketProtocol,
    base_url: str = _OPENAI_LIVE_BASE_URL,
    query_params: list[tuple[str, str]] | None = None,
) -> str:
    normalized = normalize_realtime_call_id(call_id)
    if normalized is None:
        raise ValueError("Invalid realtime call id")

    parsed = urlparse(base_url.rstrip("/"))
    base_path = parsed.path.rstrip("/")
    configured_query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
    forwarded_query_pairs = list(query_params or ())
    if protocol is RealtimeWebSocketProtocol.LIVE_V3:
        if any(key == "call_id" for key, _value in (*configured_query_pairs, *forwarded_query_pairs)):
            raise ValueError("Path-based realtime query parameters must not include call_id")
        path = f"{base_path}/live/{quote(normalized, safe='')}"
        query_pairs = [*configured_query_pairs, *forwarded_query_pairs]
    elif protocol is RealtimeWebSocketProtocol.REALTIME_V1_V2:
        if any(key == "call_id" for key, _value in (*configured_query_pairs, *forwarded_query_pairs)):
            raise ValueError("Legacy realtime query parameters must not include call_id")
        path = f"{base_path}/realtime"
        query_pairs = [*configured_query_pairs, *forwarded_query_pairs, ("call_id", normalized)]
    else:
        raise ValueError("Unsupported realtime websocket protocol")

    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        scheme = parsed.scheme
    return urlunparse(
        parsed._replace(
            scheme=scheme,
            path=path,
            query=urlencode(query_pairs, doseq=True),
        )
    )


def _ensure_responses_websocket_beta_header(headers: dict[str, str]) -> None:
    header_key = next((key for key in headers if key.lower() == "openai-beta"), "openai-beta")
    current_value = headers.get(header_key, "")
    beta_tokens = [
        token.strip()
        for token in current_value.split(",")
        if token.strip() and token.strip().lower() not in _RESPONSES_WEBSOCKET_INCOMPATIBLE_BETA_HEADERS
    ]
    if _RESPONSES_WEBSOCKET_BETA_HEADER.lower() not in {token.lower() for token in beta_tokens}:
        beta_tokens.append(_RESPONSES_WEBSOCKET_BETA_HEADER)
    headers[header_key] = ", ".join(beta_tokens)


def _pop_header_case_insensitive(headers: dict[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key in tuple(headers):
        if key.lower() != lowered:
            continue
        return headers.pop(key)
    return None


def _aiohttp_ws_close_code(websocket: Any, message: aiohttp.WSMessage) -> int | None:
    if isinstance(message.data, int):
        return message.data
    close_code = getattr(websocket, "close_code", None)
    return close_code if isinstance(close_code, int) else None


def _aiohttp_ws_close_reason(message: aiohttp.WSMessage) -> str | None:
    reason = message.extra
    return reason if isinstance(reason, str) and reason else None


def _responses_websocket_url(base_url: str) -> str:
    parsed = urlparse(f"{base_url.rstrip('/')}/codex/responses")
    if parsed.scheme == "https":
        scheme = "wss"
    elif parsed.scheme == "http":
        scheme = "ws"
    else:
        scheme = parsed.scheme
    return urlunparse(parsed._replace(scheme=scheme))


async def _connect_upstream_websocket(
    headers: dict[str, str],
    access_token: str,
    account_id: str | None,
    *,
    url: str,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
    policy: _UpstreamWebSocketPolicy,
    subprotocols: Sequence[str] = (),
) -> UpstreamWebSocket:
    settings = get_settings()
    if policy.include_responses_beta:
        upstream_headers = _build_upstream_websocket_headers(headers, access_token, account_id)
    else:
        upstream_headers = _build_upstream_live_websocket_headers(headers, access_token, account_id)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation=policy.operation,
    )
    if route is not None:
        owns_codex_client = codex_client is None
        active_codex_client = codex_client or CodexClient(create_codex_session())
        endpoint_id = route.endpoint_id
        active_route = route
        fallback_used = False
        heartbeat = settings.proxy_downstream_websocket_idle_timeout_seconds if policy.enable_routed_heartbeat else None
        protocol_kwargs = {"protocols": subprotocols} if subprotocols else {}
        try:
            opener = getattr(active_codex_client, "open_ws_with_route_metadata", None)
            if callable(opener):
                result = await opener(
                    url,
                    route=route,
                    retry_handshake_status=policy.retry_handshake_status,
                    retry_network_errors=policy.retry_routed_network_errors,
                    headers=upstream_headers,
                    timeout=settings.upstream_connect_timeout_seconds,
                    max_msg_size=settings.max_sse_event_bytes,
                    heartbeat=heartbeat,
                    **protocol_kwargs,
                )
                context = result.context
                websocket = result.websocket
                endpoint_id = result.route.endpoint_id
                active_route = result.route
                fallback_used = result.fallback_used
            else:
                context = await active_codex_client.ws_connect(
                    url,
                    route=route,
                    headers=upstream_headers,
                    timeout=settings.upstream_connect_timeout_seconds,
                    max_msg_size=settings.max_sse_event_bytes,
                    heartbeat=heartbeat,
                    **protocol_kwargs,
                )
                websocket = await context.__aenter__() if hasattr(context, "__aenter__") else context
                if not hasattr(context, "__aenter__"):
                    context = None
                endpoint_id = route.endpoint_id
        except asyncio.CancelledError:
            if owns_codex_client:
                try:
                    await active_codex_client.close()
                except Exception:
                    logger.warning("Failed to close routed websocket client after cancelled handshake")
            raise
        except CodexTransportError as exc:
            if owns_codex_client:
                await active_codex_client.close()
            error_code = exc.error_code or "upstream_unavailable"
            status_code = exc.status_code if exc.status_code is not None and 400 <= exc.status_code <= 599 else 502
            if policy.credential_safe_connect_errors:
                message = (
                    f"Upstream websocket handshake failed with HTTP {status_code}"
                    if exc.status_code is not None
                    else "Upstream websocket connection failed"
                )
            else:
                message = str(exc)
            raise ProxyResponseError(
                status_code if policy.preserve_handshake_status else 502,
                openai_error(error_code, message, error_type="server_error"),
                failure_phase="connect",
                # Carry the client's dispatch provenance across the sanitizing
                # boundary: a typed connector failure against the routed proxy
                # proves no ``response.create`` frame could have reached
                # upstream, so service-level failover may replay the request
                # on another account. TLS verification failures are stable
                # endpoint configuration errors and stay non-replayable.
                retryable_same_contract=(
                    (policy.retry_routed_network_errors and error_code == PROCESS_NETWORK_UNAVAILABLE_CODE)
                    or (exc.retryable_same_contract and not exc.is_tls_verification_failure)
                ),
                failure_detail=(
                    "proxy_connect_pre_dispatch"
                    if exc.retryable_same_contract and not exc.is_tls_verification_failure
                    else "transport_error"
                ),
                failure_exception_type=type(exc).__name__,
            ) from exc
        except Exception:
            if owns_codex_client:
                await active_codex_client.close()
            raise
        return ArchivingUpstreamWebSocket(
            CodexUpstreamWebSocket(
                websocket,
                context=context if hasattr(context, "__aenter__") else None,
                codex_client=active_codex_client,
                owns_codex_client=owns_codex_client,
                endpoint_id=endpoint_id,
                response_headers=_codex_websocket_response_headers(websocket, context),
            ),
            url=url,
            headers=upstream_headers,
            account_id=account_id,
            route=active_route,
            fallback_used=fallback_used,
            archive_payloads=policy.archive_payloads,
        )
    origin = cast(Origin | None, _pop_header_case_insensitive(upstream_headers, "origin"))
    user_agent = _pop_header_case_insensitive(upstream_headers, "user-agent")
    proxy_env = (
        settings.upstream_websocket_proxy_env() if hasattr(settings, "upstream_websocket_proxy_env") else os.environ
    )
    proxy_url = resolve_websocket_proxy_from_env(url, proxy_env) if settings.upstream_websocket_trust_env else None
    # Ping/pong control frames verify transport liveness without treating valid
    # application-frame silence as an idle response.
    ping_timeout = (
        settings.proxy_downstream_websocket_idle_timeout_seconds if policy.enable_direct_ping_timeout else None
    )
    try:
        subprotocol_kwargs = {"subprotocols": cast(Sequence[Subprotocol], subprotocols)} if subprotocols else {}
        response = await websocket_connect(
            url,
            origin=origin,
            additional_headers=upstream_headers or None,
            user_agent_header=user_agent,
            open_timeout=settings.upstream_connect_timeout_seconds,
            ping_timeout=ping_timeout,
            max_size=settings.max_sse_event_bytes,
            proxy=proxy_url,
            # Do not offer permessage-deflate upstream: the websockets library
            # enables it by default, but the sibling upstream transports (the
            # routed aiohttp path and the raw-handshake transport) already run
            # uncompressed, and per-frame zlib decode on high-rate event
            # streams burns CPU on the proxy host. The client-facing socket
            # keeps negotiating permessage-deflate per responses-api-compat.
            compression=None,
            **subprotocol_kwargs,
        )
    except asyncio.TimeoutError as exc:
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", "Request to upstream timed out"),
            failure_phase="connect",
            failure_detail=UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL,
        ) from exc
    except InvalidStatus as exc:
        response = exc.response
        is_edge_challenge = _is_upstream_edge_challenge(
            response.status_code,
            headers=response.headers,
            body=response.body,
        )
        if policy.credential_safe_connect_errors:
            status_code = response.status_code if 400 <= response.status_code <= 599 else 502
            payload = openai_error(
                "upstream_websocket_handshake_failed",
                f"Upstream websocket handshake failed with HTTP {status_code}",
                error_type="server_error",
            )
        else:
            status_code = response.status_code
            message = response.reason_phrase or f"Upstream websocket error: HTTP {status_code}"
            payload = _handshake_error_payload(status_code, message, response.headers, response.body)
        raise ProxyResponseError(
            status_code,
            payload,
            failure_phase="connect",
            # A 5xx upgrade rejection is the edge refusing to carry websockets
            # at all. The sanitized code cannot carry that provenance: this
            # policy preserves the upstream body, so the same outage surfaces
            # as ``upstream_error`` or whatever code the edge returned.
            # Credential-scoped rejections (401/403/429) stay account evidence,
            # with one narrow exception: a 403 carrying explicit Cloudflare
            # edge-challenge evidence is the edge refusing the websocket
            # upgrade itself, not a verdict on the account, so it shares the
            # transport-failure provenance and steers clients to HTTP.
            failure_detail=(
                UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL if status_code >= 500 or is_edge_challenge else None
            ),
        ) from exc
    except InvalidProxy as exc:
        message = (
            "Invalid upstream websocket proxy configuration"
            if policy.credential_safe_connect_errors
            else (str(exc) or "Invalid upstream websocket proxy configuration")
        )
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message, error_type="server_error"),
        ) from exc
    except InvalidHandshake as exc:
        message = (
            "Invalid upstream websocket handshake"
            if policy.credential_safe_connect_errors
            else (str(exc) or "Invalid upstream websocket handshake")
        )
        raise ProxyResponseError(
            502,
            openai_error("upstream_unavailable", message),
            failure_phase="connect",
            failure_detail=UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL,
        ) from exc
    except OSError as exc:
        error_code = process_network_error_code(
            exc,
            fallback="upstream_unavailable",
            include_permanent_dns=proxy_url is None,
        )
        message = "Upstream websocket connection failed" if policy.credential_safe_connect_errors else str(exc)
        raise ProxyResponseError(
            502,
            openai_error(error_code, message),
            failure_phase="connect",
            retryable_same_contract=error_code == PROCESS_NETWORK_UNAVAILABLE_CODE,
            # Host-wide network loss keeps its account-neutral process
            # recovery path, and a TLS verification failure is stable endpoint
            # configuration; neither is an upstream websocket outage that raw
            # HTTP can route around.
            failure_detail=(
                None
                if error_code == PROCESS_NETWORK_UNAVAILABLE_CODE or isinstance(exc, ssl.SSLError)
                else UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL
            ),
        ) from exc

    return ArchivingUpstreamWebSocket(
        WebsocketsUpstreamWebSocket(
            response,
            uses_proxy=proxy_url is not None,
            preserve_close_semantics=policy.preserve_close_semantics,
        ),
        url=url,
        headers=upstream_headers,
        account_id=account_id,
        direct_egress=allow_direct_egress,
        archive_payloads=policy.archive_payloads,
    )


async def connect_responses_websocket(
    headers: dict[str, str],
    access_token: str,
    account_id: str | None,
    *,
    base_url: str | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> UpstreamWebSocket:
    settings = get_settings()
    upstream_base = (base_url or settings.upstream_base_url).rstrip("/")
    return await _connect_upstream_websocket(
        headers,
        access_token,
        account_id,
        url=_responses_websocket_url(upstream_base),
        route=route,
        codex_client=codex_client,
        allow_direct_egress=allow_direct_egress,
        policy=_RESPONSES_WEBSOCKET_POLICY,
    )


async def connect_live_websocket(
    call_id: str,
    headers: dict[str, str],
    access_token: str,
    account_id: str | None,
    *,
    protocol: RealtimeWebSocketProtocol,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
    base_url: str = _OPENAI_LIVE_BASE_URL,
    query_params: list[tuple[str, str]] | None = None,
    subprotocols: Sequence[str] = (),
) -> UpstreamWebSocket:
    """Connect an account-bound Codex realtime sideband without refreshing auth."""

    return await _connect_upstream_websocket(
        headers,
        access_token,
        account_id,
        url=_live_websocket_url(
            call_id,
            protocol=protocol,
            base_url=base_url,
            query_params=query_params,
        ),
        route=route,
        codex_client=codex_client,
        allow_direct_egress=allow_direct_egress,
        policy=_LIVE_SIDEBAND_WEBSOCKET_POLICY,
        subprotocols=subprotocols,
    )


def _close_code_from_exception(exc: ConnectionClosedOK | ConnectionClosedError) -> int | None:
    if exc.rcvd is not None:
        return int(exc.rcvd.code)
    if exc.sent is not None:
        return int(exc.sent.code)
    return None


def _close_reason_from_exception(exc: ConnectionClosedOK | ConnectionClosedError) -> str | None:
    frame = exc.rcvd if exc.rcvd is not None else exc.sent
    if frame is None:
        return None
    return frame.reason or None


def _codex_websocket_response_headers(websocket: object, context: object | None) -> Mapping[str, str]:
    for source in (websocket, context):
        headers = _response_headers_from_source(source)
        if headers:
            return headers
    return {}


def _response_headers_from_source(source: object | None) -> Mapping[str, str]:
    if source is None:
        return {}
    for attr in ("response", "handshake_response"):
        response = getattr(source, attr, None)
        headers = getattr(response, "headers", None)
        if headers:
            return _normalize_response_headers(headers)
    for attr in ("headers", "response_headers"):
        headers = getattr(source, attr, None)
        if headers:
            return _normalize_response_headers(headers)
    return {}


def _normalize_response_headers(headers: Mapping[str, object] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _handshake_error_payload(
    status_code: int,
    message: str,
    headers: Headers | None = None,
    body: bytes | bytearray | None = None,
) -> OpenAIErrorEnvelope:
    parsed = _try_parse_handshake_error_payload(headers, body)
    if parsed is not None:
        return parsed
    if status_code == 401:
        return openai_error("invalid_api_key", message, error_type="authentication_error")
    if status_code == 429:
        return openai_error("rate_limit_exceeded", message, error_type="rate_limit_error")
    if status_code == 403:
        return openai_error("forbidden", message, error_type="permission_error")
    if status_code >= 500:
        return openai_error("upstream_error", message, error_type="server_error")
    return openai_error("invalid_request_error", message, error_type="invalid_request_error")


def _try_parse_handshake_error_payload(
    headers: Headers | None,
    body: bytes | bytearray | None,
) -> OpenAIErrorEnvelope | None:
    if not body:
        return None

    content_type = ""
    if headers is not None:
        content_type = headers.get("Content-Type", "")

    if "json" not in content_type.lower() and not body.strip().startswith((b"{", b"[")):
        return None

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None

    error = parse_error_payload(payload)
    if error is None:
        return None
    return {"error": _openai_error_detail(error)}


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
