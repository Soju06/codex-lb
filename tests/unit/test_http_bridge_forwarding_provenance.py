from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config.settings import get_settings
from app.core.crypto import get_or_create_key
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy.http_bridge_forwarding import (
    HTTP_BRIDGE_CODEX_AFFINITY_HEADER,
    HTTP_BRIDGE_FILE_OWNER_HEADER,
    HTTP_BRIDGE_FORWARDED_HEADER,
    HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER,
    HTTP_BRIDGE_SIGNATURE_V2_HEADER,
    HTTP_BRIDGE_TARGET_INSTANCE_HEADER,
    HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER,
    HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER,
    HTTPBridgeForwardContext,
    build_owner_forward_headers,
    parse_forwarded_request,
)


@pytest.fixture(autouse=True)
def _temp_bridge_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CODEX_LB_ENCRYPTION_KEY_FILE", str(tmp_path / "bridge.key"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _payload(text: str = "hi") -> ResponsesRequest:
    return ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": text, "input": text})


def _payload_with_file() -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "read"},
                        {"type": "input_file", "file_id": "file-owner-bound"},
                    ],
                }
            ],
        }
    )


def _provenance_body_proof_header(headers: dict[str, str]) -> str:
    exact_shape_header = "x-codex-bridge-input-shape-signature-v2"
    return exact_shape_header if exact_shape_header in headers else HTTP_BRIDGE_SIGNATURE_V2_HEADER


def _pre_provenance_tools_bound_signature(
    *,
    payload: ResponsesRequest,
    context: HTTPBridgeForwardContext,
    signature_version: str | None = None,
) -> str:
    """Frozen pre-provenance codec; independent of production helpers."""

    body_json = json.dumps(
        payload.model_dump_for_forwarding(),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    body_digest = hashlib.sha256(body_json.encode("utf-8")).hexdigest()
    signing_payload = json.dumps(
        {
            "body_digest": body_digest,
            "client_ip": context.client_ip,
            "client_ip_present": context.client_ip is not None,
            "codex_session_affinity": context.codex_session_affinity,
            "downstream_turn_state": context.downstream_turn_state,
            "file_owner_account_id": context.file_owner_account_id,
            "include_client_ip": True,
            "origin_instance": context.origin_instance,
            "original_affinity_key": context.original_affinity_key,
            "original_affinity_kind": context.original_affinity_kind,
            "original_request_unanchored": context.original_request_unanchored,
            "protocol": "codex-lb-http-bridge-forward-tools-bound",
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
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    secret = get_or_create_key(get_settings().encryption_key_file)
    return hmac.new(secret, signing_payload.encode("utf-8"), hashlib.sha256).hexdigest()


@pytest.mark.parametrize("with_file", [False, True])
def test_pre_provenance_codec_replays_both_directions(with_file: bool) -> None:
    payload = _payload_with_file() if with_file else _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=True,
        downstream_turn_state="http_turn_cross_version",
        file_owner_account_id="acc-file-owner" if with_file else None,
    )
    old_signature = _pre_provenance_tools_bound_signature(payload=payload, context=context)
    new_headers = build_owner_forward_headers(headers={}, payload=payload, context=context)

    assert new_headers[HTTP_BRIDGE_SIGNATURE_V2_HEADER] == old_signature
    assert HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER not in new_headers
    assert HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER not in new_headers

    old_headers = {
        HTTP_BRIDGE_FORWARDED_HEADER: "1",
        HTTP_BRIDGE_ORIGIN_INSTANCE_HEADER: context.origin_instance,
        HTTP_BRIDGE_TARGET_INSTANCE_HEADER: context.target_instance,
        HTTP_BRIDGE_CODEX_AFFINITY_HEADER: "1",
        HTTP_BRIDGE_SIGNATURE_V2_HEADER: old_signature,
        "x-codex-turn-state": "http_turn_cross_version",
    }
    if context.file_owner_account_id is not None:
        old_headers[HTTP_BRIDGE_FILE_OWNER_HEADER] = context.file_owner_account_id

    forwarded, error = parse_forwarded_request(old_headers, payload=payload, current_instance="instance-b")

    assert error is None
    assert forwarded is not None
    assert forwarded.context == context


def test_signed_internal_forward_preserves_generated_provenance() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        origin_instance="instance-a",
        target_instance="instance-b",
        codex_session_affinity=True,
        downstream_turn_state="http_turn_generated",
        downstream_turn_state_synthesized=True,
        reservation=ApiKeyUsageReservationData(reservation_id="res", key_id="key", model="gpt-5.4"),
    )
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)

    forwarded, error = parse_forwarded_request(headers, payload=payload, current_instance="instance-b")

    assert error is None
    assert forwarded is not None
    assert forwarded.context == context
    assert headers[HTTP_BRIDGE_SIGNATURE_V2_HEADER] == _pre_provenance_tools_bound_signature(
        payload=payload,
        context=context,
    )


def test_client_synthesized_provenance_header_is_dropped() -> None:
    context = HTTPBridgeForwardContext("instance-a", "instance-b", False, None)
    headers = build_owner_forward_headers(
        headers={HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER: "1"},
        payload=_payload(),
        context=context,
    )

    assert HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER not in headers
    assert HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER not in headers


def test_parse_forwarded_request_rejects_upgraded_turn_state_provenance() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext("instance-a", "instance-b", True, "http_turn_generated")
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers[HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER] = "1"

    forwarded, error = parse_forwarded_request(headers, payload=payload, current_instance="instance-b")

    assert forwarded is None
    assert error is not None
    assert error.payload["error"]["code"] == "bridge_forward_invalid"


@pytest.mark.parametrize("transplant", ["body-proof", "provenance"])
def test_synthesized_provenance_transplant_is_rejected(transplant: str) -> None:
    source_payload = _payload("source")
    target_payload = _payload("target")
    context = HTTPBridgeForwardContext(
        "instance-a",
        "instance-b",
        True,
        "http_turn_generated",
        downstream_turn_state_synthesized=True,
    )
    source_headers = build_owner_forward_headers(headers={}, payload=source_payload, context=context)
    target_headers = build_owner_forward_headers(headers={}, payload=target_payload, context=context)
    header = (
        _provenance_body_proof_header(target_headers)
        if transplant == "body-proof"
        else HTTP_BRIDGE_TURN_STATE_PROVENANCE_SIGNATURE_HEADER
    )
    target_headers[header] = source_headers[header]

    forwarded, error = parse_forwarded_request(target_headers, payload=target_payload, current_instance="instance-b")

    assert forwarded is None
    assert error is not None
    assert error.payload["error"]["code"] == "bridge_forward_invalid"


def test_missing_synthesized_marker_stays_explicit() -> None:
    payload = _payload()
    context = HTTPBridgeForwardContext(
        "instance-a",
        "instance-b",
        True,
        "http_turn_generated",
        downstream_turn_state_synthesized=True,
    )
    headers = build_owner_forward_headers(headers={}, payload=payload, context=context)
    headers.pop(HTTP_BRIDGE_TURN_STATE_SYNTHESIZED_HEADER)

    forwarded, error = parse_forwarded_request(headers, payload=payload, current_instance="instance-b")

    assert error is None
    assert forwarded is not None
    assert forwarded.context.downstream_turn_state_synthesized is False
