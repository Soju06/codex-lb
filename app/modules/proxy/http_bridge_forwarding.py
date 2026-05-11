from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import cast

import aiohttp

from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.crypto import get_or_create_key
from app.core.errors import OpenAIErrorEnvelope, openai_error, response_failed_event
from app.core.openai.requests import ResponsesRequest
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy.request_policy import set_policy_service_tier_metadata

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
HTTP_BRIDGE_REQUESTED_SERVICE_TIER_HEADER = "x-codex-bridge-requested-service-tier"
HTTP_BRIDGE_SERVICE_TIER_OMITTED_HEADER = "x-codex-bridge-service-tier-omitted"
HTTP_BRIDGE_SIGNATURE_HEADER = "x-codex-bridge-signature"
HTTP_BRIDGE_SIGNATURE_V2_HEADER = "x-codex-bridge-signature-v2"


@dataclass(frozen=True, slots=True)
class HTTPBridgeForwardContext:
    origin_instance: str
    target_instance: str
    codex_session_affinity: bool
    downstream_turn_state: str | None
    original_affinity_kind: str | None = None
    original_affinity_key: str | None = None
    reservation: ApiKeyUsageReservationData | None = None
    requested_service_tier: str | None = None
    service_tier_omitted: bool = False


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
    ) -> AsyncIterator[str]:
        settings = get_settings()
        timeout = _owner_forward_timeout(
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            idle_timeout_seconds=settings.stream_idle_timeout_seconds,
        )
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            async with session.post(
                f"{owner_endpoint}{HTTP_BRIDGE_INTERNAL_FORWARD_PATH}",
                json=payload.model_dump(mode="json", exclude_none=True),
                headers=build_owner_forward_headers(headers=headers, payload=payload, context=context),
            ) as response:
                if response.status != 200:
                    payload_text = await response.text()
                    raise ProxyResponseError(
                        response.status,
                        _owner_forward_error_payload(status_code=response.status, payload_text=payload_text),
                    )
                try:
                    async for event_block in _iter_sse_event_blocks(
                        response,
                        request_started_at=request_started_at,
                        proxy_request_budget_seconds=settings.proxy_request_budget_seconds,
                        stream_idle_timeout_seconds=settings.stream_idle_timeout_seconds,
                    ):
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


def build_owner_forward_headers(
    *,
    headers: Mapping[str, str],
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
) -> dict[str, str]:
    forwarded = dict(headers)
    forwarded.pop("host", None)
    forwarded.pop("content-length", None)
    forwarded[HTTP_BRIDGE_FORWARDED_HEADER] = "1"
    forwarded[HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER] = context.origin_instance
    forwarded[HTTP_BRIDGE_TARGET_INSTANCE_HEADER] = context.target_instance
    forwarded[HTTP_BRIDGE_CODEX_AFFINITY_HEADER] = "1" if context.codex_session_affinity else "0"
    if context.original_affinity_kind and context.original_affinity_key:
        forwarded[HTTP_BRIDGE_AFFINITY_KIND_HEADER] = context.original_affinity_kind
        forwarded[HTTP_BRIDGE_AFFINITY_KEY_HEADER] = context.original_affinity_key
    if context.downstream_turn_state:
        forwarded["x-codex-turn-state"] = context.downstream_turn_state
    if context.reservation is not None:
        forwarded[HTTP_BRIDGE_RESERVATION_ID_HEADER] = context.reservation.reservation_id
        forwarded[HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER] = context.reservation.key_id
        forwarded[HTTP_BRIDGE_RESERVATION_MODEL_HEADER] = context.reservation.model
    if context.requested_service_tier is not None:
        forwarded[HTTP_BRIDGE_REQUESTED_SERVICE_TIER_HEADER] = context.requested_service_tier
    forwarded[HTTP_BRIDGE_SERVICE_TIER_OMITTED_HEADER] = "1" if context.service_tier_omitted else "0"
    forwarded[HTTP_BRIDGE_SIGNATURE_HEADER] = _bridge_forward_signature(payload=payload, context=context)
    if context.requested_service_tier is not None or context.service_tier_omitted:
        forwarded[HTTP_BRIDGE_SIGNATURE_V2_HEADER] = _bridge_forward_signature_v2(payload=payload, context=context)
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
    context = HTTPBridgeForwardContext(
        origin_instance=headers.get(HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER, "").strip() or "unknown",
        target_instance=target_instance,
        codex_session_affinity=_bool_header(headers.get(HTTP_BRIDGE_CODEX_AFFINITY_HEADER)),
        downstream_turn_state=_optional_header(headers.get("x-codex-turn-state")),
        original_affinity_kind=_optional_header(headers.get(HTTP_BRIDGE_AFFINITY_KIND_HEADER)),
        original_affinity_key=_optional_header(headers.get(HTTP_BRIDGE_AFFINITY_KEY_HEADER)),
        reservation=_reservation_from_headers(headers),
        requested_service_tier=_optional_header(headers.get(HTTP_BRIDGE_REQUESTED_SERVICE_TIER_HEADER)),
        service_tier_omitted=_bool_header(headers.get(HTTP_BRIDGE_SERVICE_TIER_OMITTED_HEADER)),
    )
    signature = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_HEADER))
    expected_signature = _bridge_forward_signature(payload=payload, context=context)
    if signature is None or not hmac.compare_digest(signature, expected_signature):
        return None, ProxyResponseError(
            400,
            openai_error(
                "bridge_forward_invalid",
                "Internal bridge forward signature is invalid",
                error_type="invalid_request_error",
            ),
        )
    if context.requested_service_tier is not None or context.service_tier_omitted:
        signature_v2 = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_V2_HEADER))
        expected_signature_v2 = _bridge_forward_signature_v2(payload=payload, context=context)
        if signature_v2 is None or not hmac.compare_digest(signature_v2, expected_signature_v2):
            return None, ProxyResponseError(
                400,
                openai_error(
                    "bridge_forward_invalid",
                    "Internal bridge forward signature is invalid",
                    error_type="invalid_request_error",
                ),
            )
    if _has_service_tier_metadata_headers(headers):
        set_policy_service_tier_metadata(
            payload,
            requested_service_tier=context.requested_service_tier,
            service_tier_omitted=context.service_tier_omitted,
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


def _has_service_tier_metadata_headers(headers: Mapping[str, str]) -> bool:
    return HTTP_BRIDGE_REQUESTED_SERVICE_TIER_HEADER in headers or HTTP_BRIDGE_SERVICE_TIER_OMITTED_HEADER in headers


def _bridge_forward_signature(*, payload: ResponsesRequest, context: HTTPBridgeForwardContext) -> str:
    return _bridge_forward_signature_base(payload=payload, context=context, include_service_tier_metadata=False)


def _bridge_forward_signature_v2(*, payload: ResponsesRequest, context: HTTPBridgeForwardContext) -> str:
    return _bridge_forward_signature_base(payload=payload, context=context, include_service_tier_metadata=True)


def _bridge_forward_signature_base(
    *,
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
    include_service_tier_metadata: bool,
) -> str:
    payload_json = json.dumps(
        payload.model_dump(mode="json", exclude_none=True),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    body_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    signing_fields = [
        context.origin_instance,
        context.target_instance,
        "1" if context.codex_session_affinity else "0",
        context.downstream_turn_state or "",
        context.original_affinity_kind or "",
        context.original_affinity_key or "",
        context.reservation.reservation_id if context.reservation is not None else "",
        context.reservation.key_id if context.reservation is not None else "",
        context.reservation.model if context.reservation is not None else "",
    ]
    if include_service_tier_metadata:
        signing_fields.extend(
            (
                context.requested_service_tier or "",
                "1" if context.service_tier_omitted else "0",
            )
        )
    signing_fields.append(body_digest)
    signing_payload = "|".join(signing_fields)
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
