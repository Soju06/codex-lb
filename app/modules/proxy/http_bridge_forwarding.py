from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from typing import cast

import aiohttp

from app.core.clients.proxy import ProxyResponseError, filter_inbound_headers
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.core.config.dashboard_overrides import with_dashboard_overrides
from app.core.config.settings import get_settings
from app.core.crypto import get_or_create_key
from app.core.errors import OpenAIErrorEnvelope, openai_error, response_failed_event
from app.core.openai.requests import ResponsesRequest, extract_input_file_ids
from app.core.types import JsonObject
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.request_id import get_request_id
from app.core.utils.sse import format_sse_event
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_payload_looks_like_full_resend,
    _http_bridge_request_budget_seconds,
)
from app.modules.proxy.durable_bridge_runtime import http_bridge_owner_process_epoch

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
_LEGACY_SIGNATURE_DELIMITER = "|"

HTTP_BRIDGE_INTERNAL_FORWARD_PATH = "/internal/bridge/responses"
HTTP_BRIDGE_FORWARDED_HEADER = "x-codex-bridge-forwarded"
HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER = "x-codex-bridge-origin-instance"
HTTP_BRIDGE_TARGET_INSTANCE_HEADER = "x-codex-bridge-target-instance"
HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER = "x-codex-bridge-owner-process-epoch"
HTTP_BRIDGE_CODEX_AFFINITY_HEADER = "x-codex-bridge-codex-session-affinity"
HTTP_BRIDGE_RESERVATION_ID_HEADER = "x-codex-bridge-reservation-id"
HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER = "x-codex-bridge-reservation-key-id"
HTTP_BRIDGE_RESERVATION_MODEL_HEADER = "x-codex-bridge-reservation-model"
HTTP_BRIDGE_AFFINITY_KIND_HEADER = "x-codex-bridge-affinity-kind"
HTTP_BRIDGE_AFFINITY_KEY_HEADER = "x-codex-bridge-affinity-key"
HTTP_BRIDGE_FILE_OWNER_HEADER = "x-codex-bridge-file-owner"
HTTP_BRIDGE_ORIGINAL_UNANCHORED_HEADER = "x-codex-bridge-original-unanchored"
HTTP_BRIDGE_SIGNATURE_VERSION_HEADER = "x-codex-bridge-signature-version"
HTTP_BRIDGE_CLIENT_IP_HEADER = "x-codex-bridge-client-ip"
HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER = "x-codex-bridge-client-ip-signature"
HTTP_BRIDGE_SIGNATURE_HEADER = "x-codex-bridge-signature"
# Public full-context signature from #1203. Keep this header's codec stable:
# predecessor owners require it for file-bound forwards where primary fallback
# is forbidden. Exact input shape and owner epoch use the independent proof
# below.
HTTP_BRIDGE_SIGNATURE_V2_HEADER = "x-codex-bridge-signature-v2"
HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER = "x-codex-bridge-turn-state-synthesized"
HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER = "x-codex-bridge-turn-state-provenance-signature"
_HTTP_BRIDGE_SIGNATURE_VERSION_V2 = "2"
# Additive input-shape capability marker. Unlike the primary signature
# version, this header describes the classifier used for the request body.
# It is trusted only when the exact-body signature below authenticates the
# same value; an absent or unauthenticated marker keeps the legacy fallback.
HTTP_BRIDGE_INPUT_SHAPE_VERSION_HEADER = "x-codex-bridge-input-shape-version"
HTTP_BRIDGE_INPUT_SHAPE_SIGNATURE_HEADER = "x-codex-bridge-input-shape-signature-v2"
_HTTP_BRIDGE_INPUT_SHAPE_VERSION_V2 = "2"


@dataclass(frozen=True, slots=True)
class HTTPBridgeForwardContext:
    origin_instance: str
    target_instance: str
    codex_session_affinity: bool
    downstream_turn_state: str | None
    downstream_turn_state_synthesized: bool = False
    original_request_unanchored: bool = False
    original_affinity_kind: str | None = None
    original_affinity_key: str | None = None
    file_owner_account_id: str | None = None
    client_ip: str | None = None
    reservation: ApiKeyUsageReservationData | None = None
    signature_version: str | None = None
    expected_owner_process_epoch: str | None = None


@dataclass(frozen=True, slots=True)
class HTTPBridgeForwardedRequest:
    context: HTTPBridgeForwardContext


@dataclass(frozen=True, slots=True)
class HTTPBridgeOwnerForwardRequest:
    body: JsonObject
    headers: dict[str, str]


def build_owner_forward_request(
    *,
    body: JsonObject,
    headers: Mapping[str, str],
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
) -> HTTPBridgeOwnerForwardRequest:
    """Build the one body/header pair authenticated on the owner wire."""

    return HTTPBridgeOwnerForwardRequest(
        body=body,
        headers=build_owner_forward_headers(headers=headers, payload=payload, context=context),
    )


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


def _bridge_header_name_has_illegal_control_char(name: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in name)


def _bridge_header_value_has_illegal_control_char(value: str) -> bool:
    return any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value)


def _bridge_header_has_illegal_control_char(name: str, value: str) -> bool:
    """Return whether reconstructed metadata is unsafe as an HTTP header."""

    return _bridge_header_name_has_illegal_control_char(name) or _bridge_header_value_has_illegal_control_char(value)


def _reject_illegal_bridge_header_value(name: str, value: str | None) -> None:
    if value is None or not _bridge_header_has_illegal_control_char(name, value):
        return
    raise ProxyResponseError(
        400,
        openai_error(
            "bridge_forward_invalid",
            "Internal bridge forward metadata is not safe to forward",
            error_type="invalid_request_error",
        ),
    )


def _validate_bridge_forward_context_headers(context: HTTPBridgeForwardContext) -> None:
    for name, value in (
        (HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER, context.origin_instance),
        (HTTP_BRIDGE_TARGET_INSTANCE_HEADER, context.target_instance),
        (HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER, context.expected_owner_process_epoch),
        ("x-codex-turn-state", context.downstream_turn_state),
        (HTTP_BRIDGE_AFFINITY_KIND_HEADER, context.original_affinity_kind),
        (HTTP_BRIDGE_AFFINITY_KEY_HEADER, context.original_affinity_key),
        (HTTP_BRIDGE_FILE_OWNER_HEADER, context.file_owner_account_id),
        (HTTP_BRIDGE_CLIENT_IP_HEADER, context.client_ip),
    ):
        _reject_illegal_bridge_header_value(name, value)
    if context.reservation is None:
        return
    for name, value in (
        (HTTP_BRIDGE_RESERVATION_ID_HEADER, context.reservation.reservation_id),
        (HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER, context.reservation.key_id),
        (HTTP_BRIDGE_RESERVATION_MODEL_HEADER, context.reservation.model),
    ):
        _reject_illegal_bridge_header_value(name, value)


class HTTPBridgeOwnerClient:
    async def stream_responses(
        self,
        *,
        owner_endpoint: str,
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        context: HTTPBridgeForwardContext,
        request_started_at: float,
        owner_supports_input_shape_classifier: bool = False,
        on_request_dispatched: Callable[[], None] | None = None,
        on_response_rejected: Callable[[], None] | None = None,
        on_response_wait: Callable[[], None] | None = None,
        on_response_ready: Callable[[], None] | None = None,
        scheduler: Scheduler = REAL_SCHEDULER,
        clock: Clock = REAL_CLOCK,
    ) -> AsyncIterator[str]:
        if _http_bridge_owner_forward_requires_shape_upgrade(payload) and (
            not owner_supports_input_shape_classifier or not context.expected_owner_process_epoch
        ):
            # Classifier disagreement can either suppress required context or
            # reinject a quarantined anchor. Require current-process proof
            # before dispatch; existing local-recovery gates decide otherwise.
            raise ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_forward_failed",
                    "HTTP bridge owner cannot safely classify this continuation during a rolling upgrade",
                    error_type="server_error",
                ),
                failure_phase="owner_forward",
                failure_detail="owner_input_shape_upgrade_required",
            )
        settings = with_dashboard_overrides(get_settings())
        timeout = _owner_forward_timeout(
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            idle_timeout_seconds=settings.stream_idle_timeout_seconds,
        )
        if on_response_wait is not None:
            on_response_wait()
        async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
            request_url = f"{owner_endpoint}{HTTP_BRIDGE_INTERNAL_FORWARD_PATH}"
            request_payload = payload.model_dump_for_http_bridge_owner_forwarding()
            request_context = session.post(
                request_url,
                json=(
                    owner_request := build_owner_forward_request(
                        body=request_payload,
                        headers=headers,
                        payload=payload,
                        context=context,
                    )
                ).body,
                headers=owner_request.headers,
                skip_auto_headers=_OWNER_FORWARD_SKIP_AUTO_HEADERS,
            )
            # I/O begins when __aenter__ is awaited. Cancellation after that
            # point can leave the receiver settling the reservation.
            transport_started = False
            observed_status = False
            try:
                transport_started = True
                async with request_context as response:
                    observed_status = True
                    if response.status != 200:
                        if on_response_rejected is not None:
                            # The receiver contract never transfers cleanup on a
                            # non-200 response, so the origin may safely release.
                            on_response_rejected()
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
                            scheduler=scheduler,
                            clock=clock,
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
            except aiohttp.ClientConnectorError:
                # DNS/connect refusal never delivered the reservation.
                raise
            except asyncio.CancelledError:
                if transport_started and not observed_status and on_request_dispatched is not None:
                    on_request_dispatched()
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if not observed_status and on_request_dispatched is not None:
                    # The request left local construction and may have reached
                    # the owner; origin must not release or replay.
                    on_request_dispatched()
                raise


def _http_bridge_owner_forward_requires_shape_upgrade(payload: ResponsesRequest) -> bool:
    """Fence either classification reversal at a pre-change owner."""
    current_full_resend = _http_bridge_payload_looks_like_full_resend(payload)
    # Pre-change owners normalize raw strings before classifying. Compare the
    # normalized input's legacy item-size rule with the current raw-shape rule.
    input_value = payload.input
    if not isinstance(input_value, list):
        return False
    if len(input_value) > 1:
        return not current_full_resend
    if len(input_value) != 1:
        return current_full_resend
    try:
        legacy_full_resend = len(json.dumps(input_value[0], ensure_ascii=True, separators=(",", ":"))) >= 4096
    except (OverflowError, TypeError, ValueError):
        legacy_full_resend = False
    return current_full_resend != legacy_full_resend


def build_owner_forward_headers(
    *,
    headers: Mapping[str, str],
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
) -> dict[str, str]:
    # WebSocket client metadata is reconstructed as a header mapping without
    # passing through an HTTP parser. Validate it before signing so aiohttp is
    # never the first component to discover illegal wire bytes.
    _validate_bridge_forward_context_headers(context)
    filtered = filter_inbound_headers(headers)
    # Per the hop-by-hop contract, also drop any header named by the inbound
    # Connection header in addition to the fixed unsafe set.
    connection_value = next(
        (value for key, value in headers.items() if key.lower() == "connection"),
        "",
    )
    connection_named = {token.strip().lower() for token in connection_value.split(",") if token.strip()}
    drop = _BRIDGE_UNSAFE_HEADER_NAMES | connection_named
    # Drop any client-supplied ``x-codex-bridge-*`` header: those names are
    # reserved for the internal forward contract and are set below from the
    # trusted context. Relaying unknown bridge headers verbatim would let an
    # external client plant a spoofed ``x-codex-bridge-signature-v2`` (or any
    # other bridge header) on an honestly signed forward; upgraded origins
    # must never relay externally injected bridge headers.
    forwarded = {
        key: value
        for key, value in filtered.items()
        if key.lower() not in drop
        and not key.lower().startswith("x-codex-bridge-")
        and not _bridge_header_has_illegal_control_char(key, value)
    }
    # filter_inbound_headers strips Authorization, but the owner instance
    # re-validates the client API key from this header (see
    # _validate_internal_bridge_api_key) before swapping in its own upstream
    # access token. Preserve it so api_key_auth_enabled deployments still
    # authenticate forwarded bridge requests.
    authorization = next(
        (value for key, value in headers.items() if key.lower() == "authorization"),
        None,
    )
    if authorization is not None and not _bridge_header_has_illegal_control_char("authorization", authorization):
        forwarded["authorization"] = authorization
    forwarded[HTTP_BRIDGE_FORWARDED_HEADER] = "1"
    forwarded[HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER] = context.origin_instance
    forwarded[HTTP_BRIDGE_TARGET_INSTANCE_HEADER] = context.target_instance
    if context.expected_owner_process_epoch is not None:
        forwarded[HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER] = context.expected_owner_process_epoch
    forwarded[HTTP_BRIDGE_CODEX_AFFINITY_HEADER] = "1" if context.codex_session_affinity else "0"
    signature_version = _HTTP_BRIDGE_SIGNATURE_VERSION_V2 if context.original_request_unanchored else None
    if signature_version is not None:
        forwarded[HTTP_BRIDGE_SIGNATURE_VERSION_HEADER] = signature_version
        forwarded[HTTP_BRIDGE_ORIGINAL_UNANCHORED_HEADER] = "1"
    # Bind the exact posted body, shape marker, and optional owner epoch before
    # additive consumers select the strongest emitted body proof.
    input_shape_version = (
        None if payload._codex_lb_legacy_owner_forwarding_input_shape else _HTTP_BRIDGE_INPUT_SHAPE_VERSION_V2
    )
    if input_shape_version is not None:
        forwarded[HTTP_BRIDGE_INPUT_SHAPE_VERSION_HEADER] = input_shape_version
    forwarded[HTTP_BRIDGE_INPUT_SHAPE_SIGNATURE_HEADER] = _bridge_forward_input_shape_signature(
        payload=payload,
        context=context,
        signature_version=signature_version,
        input_shape_version=input_shape_version,
    )
    if context.original_affinity_kind and context.original_affinity_key:
        forwarded[HTTP_BRIDGE_AFFINITY_KIND_HEADER] = context.original_affinity_kind
        forwarded[HTTP_BRIDGE_AFFINITY_KEY_HEADER] = context.original_affinity_key
    if context.file_owner_account_id:
        # This proof is accepted only with the full-context signature below;
        # the legacy primary signature intentionally remains byte-compatible.
        forwarded[HTTP_BRIDGE_FILE_OWNER_HEADER] = context.file_owner_account_id
    if context.client_ip:
        forwarded[HTTP_BRIDGE_CLIENT_IP_HEADER] = context.client_ip
        if context.expected_owner_process_epoch is None:
            forwarded[HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER] = _bridge_forward_signature(
                payload=payload,
                context=context,
                include_client_ip=True,
                signature_version=signature_version,
            )
    if context.downstream_turn_state:
        forwarded["x-codex-turn-state"] = context.downstream_turn_state
    if context.reservation is not None:
        forwarded[HTTP_BRIDGE_RESERVATION_ID_HEADER] = context.reservation.reservation_id
        forwarded[HTTP_BRIDGE_RESERVATION_KEY_ID_HEADER] = context.reservation.key_id
        forwarded[HTTP_BRIDGE_RESERVATION_MODEL_HEADER] = context.reservation.model
    # ROLLOUT SHIM (#1203, remove with HTTP_BRIDGE_SIGNATURE_V2_HEADER
    # follow-up): keep sending the primary signature computed over the plain
    # ``model_dump`` (with synthesized ``"tools": []``) so owners running code
    # that predates the tamper-proofing header can still verify requests from
    # updated origins during a rolling upgrade. New-code receivers verify the
    # tamper-proofing header below first and fall back to this primary
    # signature only when the tamper-proofing header does not validate.
    # Capability-gated requests must also reject a predecessor process that
    # ignores the signed epoch after a rollback. Do not give it a valid
    # primary fallback.
    if context.expected_owner_process_epoch is None:
        forwarded[HTTP_BRIDGE_SIGNATURE_HEADER] = _bridge_forward_signature(
            payload=payload,
            context=context,
            include_client_ip=False,
            signature_version=signature_version,
        )
    # Keep the public pre-input-shape full-context proof byte-compatible. It is
    # the only file-owner proof understood by a predecessor owner, and primary
    # fallback is deliberately forbidden for file-bearing forwards. An
    # epoch-gated request must not carry any proof that a predecessor accepts.
    if context.expected_owner_process_epoch is None:
        forwarded[HTTP_BRIDGE_SIGNATURE_V2_HEADER] = _bridge_forward_tools_bound_signature(
            payload=payload,
            context=context,
            signature_version=signature_version,
        )
    return _with_bridge_turn_state_provenance(forwarded, context=context)


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
    signature_version = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_VERSION_HEADER))
    input_shape_version = _optional_header(headers.get(HTTP_BRIDGE_INPUT_SHAPE_VERSION_HEADER))
    if input_shape_version not in {None, _HTTP_BRIDGE_INPUT_SHAPE_VERSION_V2}:
        return None, _invalid_bridge_forward_signature_error()
    original_unanchored_value = _optional_header(headers.get(HTTP_BRIDGE_ORIGINAL_UNANCHORED_HEADER))
    turn_state_synthesized_value = _optional_header(headers.get(HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER))
    if turn_state_synthesized_value not in {None, "0", "1"}:
        return None, _invalid_bridge_forward_signature_error()
    if signature_version == _HTTP_BRIDGE_SIGNATURE_VERSION_V2:
        if original_unanchored_value not in {"0", "1"}:
            return None, _invalid_bridge_forward_signature_error()
        original_request_unanchored = original_unanchored_value == "1"
    elif signature_version is None:
        original_request_unanchored = False
    else:
        return None, _invalid_bridge_forward_signature_error()
    context = HTTPBridgeForwardContext(
        origin_instance=headers.get(HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER, "").strip() or "unknown",
        target_instance=target_instance,
        codex_session_affinity=_bool_header(headers.get(HTTP_BRIDGE_CODEX_AFFINITY_HEADER)),
        downstream_turn_state=_optional_header(headers.get("x-codex-turn-state")),
        downstream_turn_state_synthesized=turn_state_synthesized_value == "1",
        original_request_unanchored=original_request_unanchored,
        original_affinity_kind=_optional_header(headers.get(HTTP_BRIDGE_AFFINITY_KIND_HEADER)),
        original_affinity_key=_optional_header(headers.get(HTTP_BRIDGE_AFFINITY_KEY_HEADER)),
        file_owner_account_id=_optional_header(headers.get(HTTP_BRIDGE_FILE_OWNER_HEADER)),
        client_ip=client_ip,
        reservation=_reservation_from_headers(headers),
        signature_version=signature_version,
        expected_owner_process_epoch=_optional_header(headers.get(HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER)),
    )
    # Exact-shape fast path: a validating shape signature proves the received
    # body was not rewritten in transit —
    # including an injected ``"tools": []`` that the primary plain-dump
    # signature cannot distinguish from an omitted field — and it also
    # authenticates the full forward context (structured, delimiter-safe), so
    # it accepts even when the legacy delimiter check below would bail. It is
    # authoritative only when it validates: mere header presence is not a
    # trustworthy signal, because an external client could plant a garbage
    # value on an honestly primary-signed forward, so a present-but-invalid
    # header simply falls through to the primary verification.
    tools_bound_signature = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_V2_HEADER))
    authenticated_body_signatures: list[str] = []
    forward_authenticated = False
    tools_bound_valid = tools_bound_signature is not None and hmac.compare_digest(
        tools_bound_signature,
        _bridge_forward_tools_bound_signature(
            payload=payload,
            context=context,
            signature_version=signature_version,
        ),
    )
    input_shape_signature = _optional_header(headers.get(HTTP_BRIDGE_INPUT_SHAPE_SIGNATURE_HEADER))
    expected_input_shape_signature = _bridge_forward_input_shape_signature(
        payload=payload,
        context=context,
        signature_version=signature_version,
        input_shape_version=input_shape_version,
    )
    input_shape_valid = input_shape_signature is not None and hmac.compare_digest(
        input_shape_signature, expected_input_shape_signature
    )
    # The currently deployed shape-v2 build used the old V2 header for these
    # exact same bytes. Accept that origin layout during the controlled cutover.
    deployed_shape_v2_valid = tools_bound_signature is not None and hmac.compare_digest(
        tools_bound_signature, expected_input_shape_signature
    )
    if input_shape_valid or deployed_shape_v2_valid:
        authenticated_body_signature = input_shape_signature if input_shape_valid else tools_bound_signature
        assert authenticated_body_signature is not None
        authenticated_body_signatures.append(authenticated_body_signature)
        forward_authenticated = True
        if (
            context.expected_owner_process_epoch is not None
            and context.expected_owner_process_epoch != http_bridge_owner_process_epoch()
        ):
            return None, ProxyResponseError(
                503,
                openai_error(
                    "bridge_owner_forward_failed",
                    "Internal bridge forward reached a different owner process",
                    error_type="server_error",
                ),
            )
        payload._codex_lb_input_shape_wire_version = input_shape_version
        payload._codex_lb_legacy_owner_forwarding_input_shape = (
            input_shape_version != _HTTP_BRIDGE_INPUT_SHAPE_VERSION_V2
        )
    # Neither predecessor codec authenticates process epoch. Refuse the claim
    # before considering their proofs unless the exact-shape proof validated.
    if context.expected_owner_process_epoch is not None and not forward_authenticated:
        return None, _invalid_bridge_forward_signature_error()
    if tools_bound_valid and not input_shape_valid and not deployed_shape_v2_valid:
        payload._codex_lb_input_shape_wire_version = None
        payload._codex_lb_legacy_owner_forwarding_input_shape = True
    if tools_bound_valid:
        assert tools_bound_signature is not None
        authenticated_body_signatures.append(tools_bound_signature)
        forward_authenticated = True
    if forward_authenticated or context.downstream_turn_state_synthesized:
        return _authenticated_bridge_forward_result(
            headers=headers,
            context=context,
            authenticated_body_signatures=authenticated_body_signatures,
        )
    if context.file_owner_account_id is not None or extract_input_file_ids(payload.input):
        # The rolling-upgrade primary signature does not bind the additive
        # file-owner proof. Never allow a stripped/forged proof to downgrade to
        # it, and never allow payloads with file references to fall back after a
        # stripped proof made the owner value look absent.
        return None, _invalid_bridge_forward_signature_error()
    # ROLLOUT SHIM (#1203, remove with HTTP_BRIDGE_SIGNATURE_V2_HEADER
    # follow-up): fall back to the primary signature (#1169's versioned /
    # legacy scheme) so owners predating the tamper-proofing header — and
    # genuinely old origins that only send the primary signature — keep
    # verifying during a rolling upgrade. Known residual until the shim is
    # removed: this fallback is exactly as strong as the pre-#1203 scheme, so
    # a body-only rewrite that injects ``"tools": []`` downgrades to the
    # plain-dump digest (which is insensitive to synthesized-vs-injected empty
    # tools) and verifies. Dropping the shim restores strict tamper-proof
    # rejection.
    if signature_version is None and _legacy_signature_context_has_ambiguous_delimiter(context):
        return None, _invalid_bridge_forward_signature_error()
    signature = _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_HEADER))
    client_ip_signature = _optional_header(headers.get(HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER))
    expected_signature = _bridge_forward_signature(
        payload=payload,
        context=context,
        signature_version=signature_version,
    )
    signature_without_client_ip = _bridge_forward_signature(
        payload=payload,
        context=context,
        include_client_ip=False,
        signature_version=signature_version,
    )
    primary_signature_valid = signature is not None and hmac.compare_digest(signature, expected_signature)
    signature_without_client_ip_valid = signature is not None and hmac.compare_digest(
        signature,
        signature_without_client_ip,
    )
    client_ip_signature_valid = client_ip_signature is not None and hmac.compare_digest(
        client_ip_signature,
        expected_signature,
    )
    signature_valid = primary_signature_valid or (
        signature_without_client_ip_valid and (client_ip is None or client_ip_signature_valid)
    )
    if not signature_valid:
        return None, _invalid_bridge_forward_signature_error()
    # A pre-change origin posts the normalized one-item array for a client
    # string and has no body-bound signature that can prove the original wire
    # shape. Keep that array out of the size-based full-resend heuristic on
    # the new owner: treating its JSON overhead as client content can cross
    # the 4096-byte boundary and drop otherwise-valid prior context during a
    # rolling upgrade. Current origins send the body-bound signature and
    # preserve raw strings on the owner wire, so their classification remains
    # exact.
    # The additive marker is only authenticated by the exact-body signature.
    # A primary-signature fallback therefore remains legacy even when an
    # unauthenticated ``...-input-shape-version: 2`` header was supplied.
    payload._codex_lb_input_shape_wire_version = None
    payload._codex_lb_legacy_owner_forwarding_input_shape = True
    return HTTPBridgeForwardedRequest(context=context), None


def _invalid_bridge_forward_signature_error() -> ProxyResponseError:
    return ProxyResponseError(
        400,
        openai_error(
            "bridge_forward_invalid",
            "Internal bridge forward signature is invalid",
            error_type="invalid_request_error",
        ),
    )


def _legacy_signature_context_has_ambiguous_delimiter(context: HTTPBridgeForwardContext) -> bool:
    """Reject legacy fields whose boundaries cannot be authenticated safely."""

    values = [
        context.origin_instance,
        context.target_instance,
        context.downstream_turn_state,
        context.original_affinity_kind,
        context.original_affinity_key,
        context.client_ip,
    ]
    if context.reservation is not None:
        values.extend(
            (
                context.reservation.reservation_id,
                context.reservation.key_id,
                context.reservation.model,
            )
        )
    return any(_LEGACY_SIGNATURE_DELIMITER in value for value in values if value is not None)


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
    signature_version: str | None = None,
) -> str:
    """Primary forward signature (#1169), computed over a plain ``model_dump``.

    Because the plain dump synthesizes ``"tools": []`` for clients that
    omitted the field, this digest cannot distinguish an omitted-tools body
    from one with an injected explicit empty list; the tamper-proof binding
    lives in ``_bridge_forward_tools_bound_signature`` (#1203). This signature
    is retained for the unanchored/legacy wire contract and rolling-upgrade
    fallback. ``signature_version is None`` uses the deployed legacy
    delimiter-joined format; the versioned path uses a canonical structured
    encoding whose object boundaries make field re-packing impossible.
    """
    body_digest = _bridge_forward_body_digest(payload.model_dump(mode="json", exclude_none=True))
    if signature_version is None:
        # Preserve the deployed legacy wire format for anchored requests during
        # rolling upgrades. Its delimiter-based encoding is intentionally not
        # reused by the versioned path because attacker-controlled affinity
        # values can contain the delimiter and make different field layouts
        # authenticate alike.
        fields = [
            context.origin_instance,
            context.target_instance,
            "1" if context.codex_session_affinity else "0",
            context.downstream_turn_state or "",
            context.original_affinity_kind or "",
            context.original_affinity_key or "",
        ]
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
        signing_payload = _LEGACY_SIGNATURE_DELIMITER.join(fields)
    else:
        signing_payload = _structured_bridge_signing_payload(
            body_digest=body_digest,
            context=context,
            include_client_ip=include_client_ip,
            signature_version=signature_version,
            protocol="codex-lb-http-bridge-forward",
        )
    return _sign_bridge_payload(signing_payload)


def _bridge_forward_tools_bound_signature(
    *,
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
    signature_version: str | None = None,
) -> str:
    """Public pre-input-shape full-context signature.

    Keep this codec byte-compatible for predecessor file-owner forwards. The
    independent exact-shape proof below owns the posted-body, marker, and epoch
    binding added by this change.
    """
    body_digest = _bridge_forward_body_digest(payload.model_dump_for_forwarding())
    signing_payload = _structured_bridge_signing_payload(
        body_digest=body_digest,
        context=context,
        include_client_ip=True,
        signature_version=signature_version,
        protocol="codex-lb-http-bridge-forward-tools-bound",
    )
    return _sign_bridge_payload(signing_payload)


def _bridge_forward_input_shape_signature(
    *,
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
    signature_version: str | None = None,
    input_shape_version: str | None = _HTTP_BRIDGE_INPUT_SHAPE_VERSION_V2,
) -> str:
    """Authenticate the exact owner body, input shape, and optional epoch."""

    body_digest = _bridge_forward_body_digest(payload.model_dump_for_http_bridge_owner_forwarding())
    signing_payload = _structured_bridge_signing_payload(
        body_digest=body_digest,
        context=context,
        include_client_ip=True,
        signature_version=signature_version,
        protocol="codex-lb-http-bridge-forward-tools-bound",
        input_shape_version=input_shape_version,
        include_owner_process_epoch=True,
    )
    return _sign_bridge_payload(signing_payload)


def _bridge_forward_body_digest(payload_dump: JsonObject) -> str:
    payload_json = json.dumps(payload_dump, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _with_bridge_turn_state_provenance(
    forwarded: dict[str, str],
    *,
    context: HTTPBridgeForwardContext,
) -> dict[str, str]:
    """Bind synthesized-state privilege to the strongest emitted body proof."""

    if not context.downstream_turn_state_synthesized:
        return forwarded
    authenticated_body_signature = _bridge_forward_provenance_body_signature(forwarded)
    assert authenticated_body_signature is not None
    forwarded[HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER] = "1"
    forwarded[HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER] = _bridge_forward_turn_state_provenance_signature(
        context=context,
        authenticated_body_signature=authenticated_body_signature,
    )
    return forwarded


def _authenticated_bridge_forward_result(
    *,
    headers: Mapping[str, str],
    context: HTTPBridgeForwardContext,
    authenticated_body_signatures: list[str],
) -> tuple[HTTPBridgeForwardedRequest | None, ProxyResponseError | None]:
    """Grant synthesized-state privilege only for the authenticated body proof."""

    if context.downstream_turn_state_synthesized:
        provenance_signature = _optional_header(headers.get(HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER))
        provenance_valid = provenance_signature is not None and any(
            hmac.compare_digest(
                provenance_signature,
                _bridge_forward_turn_state_provenance_signature(
                    context=context,
                    authenticated_body_signature=authenticated_body_signature,
                ),
            )
            for authenticated_body_signature in authenticated_body_signatures
        )
        if not provenance_valid:
            return None, _invalid_bridge_forward_signature_error()
    return HTTPBridgeForwardedRequest(context=context), None


def _bridge_forward_turn_state_provenance_signature(
    *,
    context: HTTPBridgeForwardContext,
    authenticated_body_signature: str,
) -> str:
    """Bind generated-state privilege to body-proof bytes that already validate."""

    signing_payload = json.dumps(
        {
            "downstream_turn_state_synthesized": context.downstream_turn_state_synthesized,
            "protocol": "codex-lb-http-bridge-turn-state-provenance-v1",
            # Keep the field name and wire bytes stable for the pre-shape V2
            # proof. The value may also be the independently validated exact-
            # shape proof when that additive contract is present.
            "tools_bound_signature": authenticated_body_signature,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sign_bridge_payload(signing_payload)


def _bridge_forward_provenance_body_signature(headers: Mapping[str, str]) -> str | None:
    """Select the strongest emitted body proof for synthesized provenance."""

    exact_shape_signature = _optional_header(headers.get("x-codex-bridge-input-shape-signature-v2"))
    if exact_shape_signature is not None:
        return exact_shape_signature
    return _optional_header(headers.get(HTTP_BRIDGE_SIGNATURE_V2_HEADER))


def _structured_bridge_signing_payload(
    *,
    body_digest: str,
    context: HTTPBridgeForwardContext,
    include_client_ip: bool,
    signature_version: str | None,
    protocol: str,
    input_shape_version: str | None = None,
    include_owner_process_epoch: bool = False,
) -> str:
    # Canonical structured encoding: object boundaries make field re-packing
    # impossible, the client-IP mode is itself authenticated, and ``protocol``
    # domain-separates the primary and tamper-proofing signatures.
    signing_fields = {
        "body_digest": body_digest,
        "client_ip": context.client_ip if include_client_ip else None,
        "client_ip_present": context.client_ip is not None,
        "codex_session_affinity": context.codex_session_affinity,
        "downstream_turn_state": context.downstream_turn_state,
        "file_owner_account_id": context.file_owner_account_id,
        "include_client_ip": include_client_ip,
        "origin_instance": context.origin_instance,
        "original_affinity_key": context.original_affinity_key,
        "original_affinity_kind": context.original_affinity_kind,
        "original_request_unanchored": context.original_request_unanchored,
        "protocol": protocol,
        "reservation": (
            {
                "id": context.reservation.reservation_id,
                "key_id": context.reservation.key_id,
                "model": context.reservation.model,
            }
            if context.reservation is not None
            else None
        ),
        "signature_version": signature_version,
        "target_instance": context.target_instance,
    }
    if input_shape_version is not None:
        signing_fields["input_shape_version"] = input_shape_version
    if include_owner_process_epoch and context.expected_owner_process_epoch is not None:
        signing_fields["expected_owner_process_epoch"] = context.expected_owner_process_epoch
    return json.dumps(
        signing_fields,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sign_bridge_payload(signing_payload: str) -> str:
    secret = get_or_create_key(get_settings().encryption_key_file)
    return hmac.new(secret, signing_payload.encode("utf-8"), hashlib.sha256).hexdigest()


async def _iter_sse_event_blocks(
    response: aiohttp.ClientResponse,
    *,
    request_started_at: float,
    proxy_request_budget_seconds: float,
    stream_idle_timeout_seconds: float,
    scheduler: Scheduler,
    clock: Clock,
) -> AsyncIterator[str]:
    buffer = b""
    chunks = response.content.iter_chunked(65536)
    while True:
        receive_timeout = _owner_forward_receive_timeout(
            request_started_at=request_started_at,
            proxy_request_budget_seconds=proxy_request_budget_seconds,
            stream_idle_timeout_seconds=stream_idle_timeout_seconds,
            now=clock.monotonic(),
        )
        try:
            chunk = await scheduler.wait_for(chunks.__anext__(), timeout=receive_timeout.timeout_seconds)
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
    now: float,
) -> _OwnerForwardReceiveTimeout:
    idle_timeout_seconds = max(0.001, stream_idle_timeout_seconds)
    remaining_budget = max(0.0, request_started_at + proxy_request_budget_seconds - now)
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
