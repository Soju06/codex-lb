from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import cast

import aiohttp

from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.config.settings import get_settings
from app.core.crypto import get_or_create_key
from app.core.errors import OpenAIErrorEnvelope, openai_error, response_failed_event
from app.core.openai.requests import ResponsesRequest
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_request_budget_seconds

# HTTP-only and hop-by-hop headers that must not be forwarded through the
# internal bridge. These headers are either illegal in WebSocket handshakes or
# carry HTTP framing semantics that the aiohttp upstream session manages itself.
# Applies on top of filter_inbound_headers (which already strips authorization,
# host, content-length, and x-forwarded-* / cf-* headers).
_BRIDGE_UNSAFE_HEADER_NAMES = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-type",
        "cookie",
        "keep-alive",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_OWNER_FORWARD_SKIP_AUTO_HEADERS = frozenset({aiohttp.hdrs.ACCEPT, aiohttp.hdrs.ACCEPT_ENCODING})

HTTP_BRIDGE_INTERNAL_FORWARD_PATH = "/internal/bridge/responses"
HTTP_BRIDGE_FORWARDED_HEADER = "x-codex-bridge-forwarded"
HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER = "x-codex-bridge-origin-instance"
HTTP_BRIDGE_TARGET_INSTANCE_HEADER = "x-codex-bridge-target-instance"
HTTP_BRIDGE_CODEX_AFFINITY_HEADER = "x-codex-bridge-codex-session-affinity"
HTTP_BRIDGE_RESERVATION_ID_HEADER = "x-codex-bridge-reservation-id"
HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER = "x-codex-bridge-reservation-key-id"
HTTP_BRIDGE_RESERVATION_MODEL_HEADER = "x-codex-bridge-reservation-model"
HTTP_BRIDGE_AFFINITY_KIND_HEADER = "x-codex-bridge-affinity-kind"
HTTP_BRIDGE_AFFINITY_KEY_HEADER = "x-codex-bridge-affinity-key"
HTTP_BRIDGE_CLIENT_IP_HEADER = "x-codex-bridge-client-ip"
HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER = "x-codex-bridge-client-ip-signature"
HTTP_BRIDGE_SIGNATURE_HEADER = "x-codex-bridge-signature"
# v2 signs the exact forwarding serialization posted as the body; the v1
# header above is kept only as a one-release rolling-upgrade shim (see the
# ROLLOUT SHIM notes in ``build_owner_forward_headers`` and
# ``parse_forwarded_request``).
HTTP_BRIDGE_SIGNATURE_V2_HEADER = "x-codex-bridge-signature-v2"


@dataclass(frozen=True, slots=True)
class HTTPBridgeForwardContext:
    origin_instance: str
    target_instance: str
    codex_session_affinity: bool
    downstream_turn_state: str | None
    original_affinity_kind: str | None = None
    original_affinity_key: str | None = None
    client_ip: str | None = None
    reservation: ApiKeyUsageReservationData | None = None


@dataclass(frozen=True, slots=True)
class HTTPBridgeForwardedRequest:
    context: HTTPBridgeForwardContext


@dataclass(frozen=True, slots=True)
class _OwnerForwardReceiveTimeout:
    timeout_seconds: float
    error_code: str
    error_message: str


class _OwnerForwardStreamTimeoutError(Exception):
    def __init__(self, *, error_code: str, error_message: str) -> None:
        super().__init__(error_message)
        self.error_code = error_code
        self.error_message = error_message


@dataclass(frozen=True, slots=True)
class OwnerForwardRelayFailure(Exception):
    event_block: str


class HTTPBridgeOwnerClient:
    async def stream_responses(
        self,
        *,
        owner_endpoint: str,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        context: HTTPBridgeForwardContext,
        request_started_at: float,
        on_response_ready: Callable[[], None] | None = None,
    ) -> AsyncIterator[str]:
        settings = get_settings()
        timeout = _owner_forward_timeout(
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            idle_timeout_seconds=settings.stream_idle_timeout_seconds,
        )
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.post(
                f"{owner_endpoint}{HTTP_BRIDGE_INTERNAL_FORWARD_PATH}",
                json=payload.model_dump_for_forwarding(),
                headers=build_owner_forward_headers(headers=headers, payload=payload, context=context),
                skip_auto_headers=_OWNER_FORWARD_SKIP_AUTO_HEADERS,
            ) as response:
                if response.status != 200:
                    payload_text = await response.text()
                    raise ProxyResponseError(
                        response.status,
                        _owner_forward_error_payload(status_code=response.status, payload_text=payload_text),
                        failure_phase="owner_forward_status",
                        failure_detail="owner_forward_non_200",
                        upstream_status_code=response.status,
                    )
                if on_response_ready is not None:
                    on_response_ready()
                yielded_event = False
                try:
                    async for event_block in _iter_sse_event_blocks(
                        response,
                        request_started_at=request_started_at,
                        proxy_request_budget_seconds=_http_bridge_request_budget_seconds(settings),
                        stream_idle_timeout_seconds=settings.stream_idle_timeout_seconds,
                    ):
                        yielded_event = True
                        yield event_block
                except _OwnerForwardStreamTimeoutError as exc:
                    raise OwnerForwardRelayFailure(
                        format_sse_event(
                            response_failed_event(
                                exc.error_code,
                                exc.error_message,
                                response_id=get_request_id(),
                            )
                        )
                    )
                if not yielded_event:
                    yield format_sse_event(
                        response_failed_event(
                            "stream_incomplete",
                            "Upstream websocket closed before response.completed",
                            response_id=get_request_id(),
                        )
                    )


def build_owner_forward_headers(
    *,
    headers: Mapping[str, str],
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
) -> dict[str, str]:
    filtered = filter_inbound_headers(headers)
    # Per the hop-by-hop contract, also drop any header named by the inbound
    # Connection header in addition to the fixed unsafe set.
    connection_value = next(
        (value for key, value in headers.items() if key.lower() == "connection"),
        "",
    )
    connection_named = {token.strip().lower() for token in connection_value.split(",") if token.strip()}
    drop = _BRIDGE_UNSAFE_HEADER_NAMES | connection_named
    forwarded = {key: value for key, value in filtered.items() if key.lower() not in drop}
    # filter_inbound_headers strips Authorization, but the owner instance
    # re-validates the client API key from this header (see
    # _validate_internal_bridge_api_key) before swapping in its own upstream
    # access token. Preserve it so api_key_auth_enabled deployments still
    # authenticate forwarded bridge requests.
    authorization = next(
        (value for key, value in headers.items() if key.lower() == "authorization"),
        None,
    )
    if authorization is not None:
        forwarded["authorization"] = authorization
    forwarded[HTTP_BRIDGE_FORWARDED_HEADER] = "1"
    forwarded[HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER] = context.origin_instance
    forwarded[HTTP_BRIDGE_TARGET_INSTANCE_HEADER] = context.target_instance
    forwarded[HTTP_BRIDGE_CODEX_AFFINITY_HEADER] = "1" if context.codex_session_affinity else "0"
    if context.original_affinity_kind and context.original_affinity_key:
        forwarded[HTTP_BRIDGE_AFFINITY_KIND_HEADER] = context.original_affinity_kind
        forwarded[HTTP_BRIDGE_AFFINITY_KEY_HEADER] = context.original_affinity_key
    if context.client_ip:
        forwarded[HTTP_BRIDGE_CLIENT_IP_HEADER] = context.client_ip
        forwarded[HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER] = _bridge_forward_signature(
            payload=payload,
            context=context,
            include_client_ip=True,
        )
    if context.downstream_turn_state:
        forwarded["x-codex-turn-state"] = context.downstream_turn_state
    if context.reservation is not None:
        forwarded[HTTP_BRIDGE_RESERVATION_ID_HEADER] = context.reservation.reservation_id
        forwarded[HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER] = context.reservation.key_id
        forwarded[HTTP_BRIDGE_RESERVATION_MODEL_HEADER] = context.reservation.model
    # ROLLOUT SHIM (#1203, remove with HTTP_BRIDGE_SIGNATURE_V2_HEADER
    # follow-up): keep sending the legacy v1 signature headers, computed over
    # the plain ``model_dump`` (with synthesized ``"tools": []``), so
    # old-code owners can still verify requests from updated origins during
    # a rolling upgrade. New-code owners verify only the v2 header below.
    forwarded[HTTP_BRIDGE_SIGNATURE_HEADER] = _bridge_forward_signature(
        payload=payload,
        context=context,
        include_client_ip=False,
    )
    forwarded[HTTP_BRIDGE_SIGNATURE_V2_HEADER] = _bridge_forward_signature_v2(
        payload=payload,
        context=context,
    )
    return forwarded


def parse_forwarded_request(
    headers: Mapping[str, str],
    *,
    payload: ResponsesRequest,
    current_instance: str,
) -> tuple[HTTPBridgeForwardedRequest | None, ProxyResponseError | None]:
    if headers.get(HTTP_BRIDGE_FORWARDED_HEADER) != "1":
        return None, ProxyResponseError(
            400,
            openai_error(
                "bridge_forward_invalid",
                "Internal bridge forward marker is required",
                error_type="invalid_request_error",
            ),
        )
    target_instance = headers.get(HTTP_BRIDGE_TARGET_INSTANCE_HEADER, "").strip()
    if not target_instance or target_instance != current_instance:
        return None, ProxyResponseError(
            503,
            openai_error(
                "bridge_owner_forward_failed",
                "Internal bridge forward reached a non-target instance",
                error_type="server_error",
            ),
        )
    client_ip = _optional_header(headers.get(HTTP_BRIDGE_CLIENT_IP_HEADER))
    context = HTTPBridgeForwardContext(
        origin_instance=headers.get(HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER, "").strip() or "unknown",
        target_instance=target_instance,
        codex_session_affinity=_bool_header(headers.get(HTTP_BRIDGE_CODEX_AFFINITY_HEADER)),
        downstream_turn_state=_optional_header(headers.get("x-codex-turn-state")),
        original_affinity_kind=_optional_header(headers.get(HTTP_BRIDGE_AFFINITY_KIND_HEADER)),
        original_affinity_key=_optional_header(headers.get(HTTP_BRIDGE_AFFINITY_KEY_HEADER)),
        client_ip=client_ip,
        reservation=_reservation_from_headers(headers),
    )
    signature_v2 = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_V2_HEADER))
    if signature_v2 is not None:
        # v2 signs the exact forwarding serialization posted as the body, so
        # verifying it against the received (re-serialized) body rejects any
        # in-transit rewrite — including an injected ``"tools": []`` that the
        # legacy digest cannot distinguish from an omitted field. When the
        # header is present it is the only signature consulted.
        signature_valid = hmac.compare_digest(
            signature_v2,
            _bridge_forward_signature_v2(payload=payload, context=context),
        )
    else:
        # ROLLOUT SHIM (#1203, remove with HTTP_BRIDGE_SIGNATURE_V2_HEADER
        # follow-up): an origin still running pre-v2 code signs only the
        # legacy headers over the plain ``model_dump``. Accept those during a
        # rolling upgrade; drop this branch (and stop sending the v1 headers)
        # once the fleet is homogeneous on a v2-signing release.
        signature = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_HEADER))
        client_ip_signature = _optional_header(headers.get(HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER))
        expected_signature = _bridge_forward_signature(payload=payload, context=context)
        legacy_signature = _bridge_forward_signature(
            payload=payload,
            context=context,
            include_client_ip=False,
        )
        primary_signature_valid = signature is not None and hmac.compare_digest(signature, expected_signature)
        legacy_signature_valid = signature is not None and hmac.compare_digest(signature, legacy_signature)
        client_ip_signature_valid = client_ip_signature is not None and hmac.compare_digest(
            client_ip_signature,
            expected_signature,
        )
        signature_valid = primary_signature_valid or (
            legacy_signature_valid and (client_ip is None or client_ip_signature_valid)
        )
    if not signature_valid:
        return None, ProxyResponseError(
            400,
            openai_error(
                "bridge_forward_invalid",
                "Internal bridge forward signature is invalid",
                error_type="invalid_request_error",
            ),
        )
    return HTTPBridgeForwardedRequest(context=context), None


def _owner_forward_timeout(*, connect_timeout_seconds: float, idle_timeout_seconds: float) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(
        total=None,
        sock_connect=connect_timeout_seconds,
        sock_read=max(0.001, idle_timeout_seconds),
    )


def _reservation_from_headers(headers: Mapping[str, str]) -> ApiKeyUsageReservationData | None:
    reservation_id = _optional_header(headers.get(HTTP_BRIDGE_RESERVATION_ID_HEADER))
    key_id = _optional_header(headers.get(HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER))
    model = _optional_header(headers.get(HTTP_BRIDGE_RESERVATION_MODEL_HEADER))
    if reservation_id is None or key_id is None or model is None:
        return None
    return ApiKeyUsageReservationData(
        reservation_id=reservation_id,
        key_id=key_id,
        model=model,
    )


def _bool_header(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_header(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _bridge_forward_signature(
    *,
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
    include_client_ip: bool = True,
) -> str:
    # Legacy (v1) computation over a plain ``model_dump``, which synthesizes
    # ``"tools": []`` for clients that omitted the field — so it cannot
    # distinguish an omitted-tools body from one with an injected explicit
    # empty list. Kept only so old-code owners can verify requests from
    # updated origins during a rolling upgrade; new-code receivers prefer
    # ``_bridge_forward_signature_v2`` (see the ROLLOUT SHIM note in
    # ``parse_forwarded_request``).
    payload_json = json.dumps(
        payload.model_dump(mode="json", exclude_none=True),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _signed_bridge_forward_digest(
        payload_json=payload_json,
        context=context,
        include_client_ip=include_client_ip,
        version=None,
    )


def _bridge_forward_signature_v2(
    *,
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
) -> str:
    # Sign the same forwarding dump that is actually posted as the request
    # body (``model_dump_for_forwarding``), not a plain ``model_dump`` that
    # synthesizes ``"tools": []`` for clients that omitted the field. A plain
    # dump would make the omitted-tools and explicit-``tools: []`` bodies
    # sign identically, so a body rewritten in transit to inject
    # ``"tools": []`` would still verify on the owner instance and re-mark
    # ``tools`` as explicitly set (issue #1184). With the forwarding dump the
    # receiving side re-serializes the parsed body it actually received, and
    # any injected or stripped ``tools`` field changes the digest. The "v2"
    # version tag domain-separates this digest from the legacy headers so a
    # v1 signature value can never be replayed in the v2 header.
    payload_json = json.dumps(
        payload.model_dump_for_forwarding(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _signed_bridge_forward_digest(
        payload_json=payload_json,
        context=context,
        include_client_ip=True,
        version="v2",
    )


def _signed_bridge_forward_digest(
    *,
    payload_json: str,
    context: HTTPBridgeForwardContext,
    include_client_ip: bool,
    version: str | None,
) -> str:
    body_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    fields = [] if version is None else [version]
    fields.extend(
        (
            context.origin_instance,
            context.target_instance,
            "1" if context.codex_session_affinity else "0",
            context.downstream_turn_state or "",
            context.original_affinity_kind or "",
            context.original_affinity_key or "",
        )
    )
    if include_client_ip:
        fields.append(context.client_ip or "")
    fields.extend(
        (
            context.reservation.reservation_id if context.reservation is not None else "",
            context.reservation.key_id if context.reservation is not None else "",
            context.reservation.model if context.reservation is not None else "",
            body_digest,
        )
    )
    signing_payload = "|".join(fields)
    secret = get_or_create_key(get_settings().encryption_key_file)
    return hmac.new(secret, signing_payload.encode("utf-8"), hashlib.sha256).hexdigest()


async def _iter_sse_event_blocks(
    response: aiohttp.ClientResponse,
    *,
    request_started_at: float,
    proxy_request_budget_seconds: float,
    stream_idle_timeout_seconds: float,
) -> AsyncIterator[str]:
    buffer = b""
    chunks = response.content.iter_chunked(65536)
    while True:
        receive_timeout = _owner_forward_receive_timeout(
            request_started_at=request_started_at,
            proxy_request_budget_seconds=proxy_request_budget_seconds,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
        )
        try:
            chunk = await asyncio.wait_for(chunks.__anext__(), timeout=receive_timeout.timeout_seconds)
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError as exc:
            raise _OwnerForwardStreamTimeoutError(
                error_code=receive_timeout.error_code,
                error_message=receive_timeout.error_message,
            ) from exc
        if not chunk:
            continue
        buffer += chunk
        while b"\n\n" in buffer:
            raw_block, buffer = buffer.split(b"\n\n", 1)
            text = raw_block.decode("utf-8")
            if text:
                yield f"{text}\n\n"
    if buffer.strip():
        yield buffer.decode("utf-8")


def _owner_forward_receive_timeout(
    *,
    request_started_at: float,
    proxy_request_budget_seconds: float,
    stream_idle_timeout_seconds: float,
) -> _OwnerForwardReceiveTimeout:
    idle_timeout_seconds = max(0.001, stream_idle_timeout_seconds)
    remaining_budget = _remaining_budget_seconds(request_started_at + proxy_request_budget_seconds)
    idle_timeout_matches_request_budget = idle_timeout_seconds == max(0.001, proxy_request_budget_seconds)
    if remaining_budget <= 0 and idle_timeout_matches_request_budget:
        return _OwnerForwardReceiveTimeout(
            timeout_seconds=0.0,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
        )
    if idle_timeout_matches_request_budget and remaining_budget >= idle_timeout_seconds:
        return _OwnerForwardReceiveTimeout(
            timeout_seconds=remaining_budget,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
        )
    if remaining_budget <= 0:
        return _OwnerForwardReceiveTimeout(
            timeout_seconds=0.0,
            error_code="upstream_request_timeout",
            error_message="Proxy request budget exhausted",
        )
    if idle_timeout_seconds <= remaining_budget:
        return _OwnerForwardReceiveTimeout(
            timeout_seconds=idle_timeout_seconds,
            error_code="stream_idle_timeout",
            error_message="Upstream stream idle timeout",
        )
    return _OwnerForwardReceiveTimeout(
        timeout_seconds=remaining_budget,
        error_code="upstream_request_timeout",
        error_message="Proxy request budget exhausted",
    )


def _remaining_budget_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _owner_forward_error_payload(*, status_code: int, payload_text: str) -> OpenAIErrorEnvelope:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        payload = None
    if is_json_mapping(payload) and is_json_mapping(payload.get("error")):
        return cast(OpenAIErrorEnvelope, payload)
    return openai_error(
        "bridge_owner_forward_failed",
        payload_text or f"HTTP bridge owner request failed with status {status_code}",
        error_type="server_error",
    )
