from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.openai.requests import ResponsesRequest
from app.modules.proxy import http_bridge_forwarding as forwarding


@pytest.fixture(autouse=True)
def _bridge_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("CODEX_LB_ENCRYPTION_KEY_FILE", str(tmp_path / "bridge.key"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _context() -> forwarding.HTTPBridgeForwardContext:
    return forwarding.HTTPBridgeForwardContext(
        origin_instance="origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
        client_ip="192.0.2.1",
        expected_owner_process_epoch="proven-process",
    )


def _payload() -> ResponsesRequest:
    return ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": "x" * 4095},
    )


@pytest.mark.parametrize(
    ("text_length", "requires_upgrade"),
    [(4091, False), (4092, True), (4093, True), (4094, False)],
)
def test_one_item_array_compatibility_boundary(text_length: int, requires_upgrade: bool) -> None:
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": ["x" * text_length]},
    )
    assert forwarding._http_bridge_owner_forward_requires_shape_upgrade(payload) is requires_upgrade


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement_epoch", [False, True], ids=["same-process", "replacement-process"])
@pytest.mark.parametrize(
    "input_value",
    [
        "x" * 4095,
        ["x" * 4092],
        ["x" * 4093],
        [{"role": "system", "content": "x" * 4096}],
        [{"role": "developer", "content": "x" * 4096}],
        [{"role": "system", "content": "a"}, {"role": "developer", "content": "b"}],
        [
            {"type": "function_call_output", "call_id": "call-1", "output": "first"},
            {"type": "function_call_output", "call_id": "call-2", "output": "second"},
        ],
    ],
    ids=[
        "raw-string",
        "array-boundary",
        "array-boundary-upper",
        "large-system",
        "large-developer",
        "multi-instructions",
        "parallel-tool-outputs",
    ],
)
async def test_owner_forward_checks_proven_epoch_at_http_receive(
    monkeypatch: pytest.MonkeyPatch,
    replacement_epoch: bool,
    input_value: object,
) -> None:
    accepted = False
    rejected = False
    received = False
    payload = ResponsesRequest.model_validate(
        {"model": "gpt-5.4", "instructions": "hi", "input": input_value},
    )
    context = _context()
    # Capability proof was for this epoch, but a new process can occupy the
    # same endpoint by the time the HTTP request arrives.
    monkeypatch.setattr(
        forwarding,
        "http_bridge_owner_process_epoch",
        lambda: "replacement-process" if replacement_epoch else "proven-process",
    )

    async def receive(request: web.Request) -> web.Response:
        nonlocal accepted, received
        received = True
        assert request.headers[forwarding.HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER] == "proven-process"
        assert forwarding.HTTP_BRIDGE_SIGNATURE_HEADER not in request.headers
        assert forwarding.HTTP_BRIDGE_CLIENT_IP_SIGNATURE_HEADER not in request.headers
        assert forwarding.HTTP_BRIDGE_SIGNATURE_V2_HEADER not in request.headers
        assert forwarding.HTTP_BRIDGE_INPUT_SHAPE_SIGNATURE_HEADER in request.headers
        wire_payload = ResponsesRequest.model_validate(await request.json())
        forwarded, error = forwarding.parse_forwarded_request(
            request.headers,
            payload=wire_payload,
            current_instance="owner",
        )
        if error is not None:
            return web.json_response(error.payload, status=error.status_code)
        assert forwarded is not None
        assert not wire_payload._codex_lb_legacy_owner_forwarding_input_shape
        assert forwarding._http_bridge_payload_looks_like_full_resend(wire_payload) == (
            forwarding._http_bridge_payload_looks_like_full_resend(payload)
        )
        accepted = True
        return web.Response(
            text='data: {"type":"response.completed","response":{"id":"resp-test","status":"completed"}}\n\n',
            content_type="text/event-stream",
        )

    def response_rejected() -> None:
        nonlocal rejected
        rejected = True

    app = web.Application()
    app.router.add_post(forwarding.HTTP_BRIDGE_INTERNAL_FORWARD_PATH, receive)
    async with TestServer(app) as server:
        stream = forwarding.HTTPBridgeOwnerClient().stream_responses(
            owner_endpoint=str(server.make_url("")).rstrip("/"),
            payload=payload,
            headers={},
            context=context,
            request_started_at=time.monotonic(),
            owner_supports_input_shape_classifier=True,
            on_response_rejected=response_rejected,
        )
        if replacement_epoch:
            with pytest.raises(ProxyResponseError) as exc_info:
                _ = [event async for event in stream]
            assert exc_info.value.status_code == 503
            assert exc_info.value.payload["error"]["code"] == "bridge_owner_forward_failed"
        else:
            events = [event async for event in stream]
            assert len(events) == 1
            assert "response.completed" in events[0]
    assert received
    assert accepted is not replacement_epoch
    assert rejected is replacement_epoch


@pytest.mark.parametrize(
    "tamper",
    ["strip-epoch", "change-epoch", "strip-shape-marker", "strip-exact-signature", "primary-only"],
)
def test_epoch_bound_forward_cannot_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    tamper: str,
) -> None:
    payload = _payload()
    context = _context()
    headers = forwarding.build_owner_forward_headers(headers={}, payload=payload, context=context)
    monkeypatch.setattr(forwarding, "http_bridge_owner_process_epoch", lambda: "proven-process")
    if tamper == "strip-epoch":
        headers.pop(forwarding.HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER)
    elif tamper == "change-epoch":
        headers[forwarding.HTTP_BRIDGE_OWNER_PROCESS_EPOCH_HEADER] = "different-process"
    elif tamper == "strip-shape-marker":
        headers.pop(forwarding.HTTP_BRIDGE_INPUT_SHAPE_VERSION_HEADER)
    else:
        headers.pop(forwarding.HTTP_BRIDGE_INPUT_SHAPE_SIGNATURE_HEADER)
        if tamper == "primary-only":
            headers[forwarding.HTTP_BRIDGE_SIGNATURE_HEADER] = forwarding._bridge_forward_signature(
                payload=payload,
                context=context,
            )
    forwarded, error = forwarding.parse_forwarded_request(headers, payload=payload, current_instance="owner")
    assert forwarded is None
    assert error is not None
    assert error.status_code == 400


@pytest.mark.asyncio
async def test_capability_boolean_without_epoch_does_not_authorize_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_session(**_kwargs: object) -> None:
        pytest.fail("A capability boolean without a signed epoch must not dispatch")

    monkeypatch.setattr(forwarding.aiohttp, "ClientSession", unexpected_session)
    context = forwarding.HTTPBridgeForwardContext(
        origin_instance="origin",
        target_instance="owner",
        codex_session_affinity=False,
        downstream_turn_state=None,
    )
    with pytest.raises(ProxyResponseError) as exc_info:
        _ = [
            event
            async for event in forwarding.HTTPBridgeOwnerClient().stream_responses(
                owner_endpoint="http://owner",
                payload=_payload(),
                headers={},
                context=context,
                request_started_at=time.monotonic(),
                owner_supports_input_shape_classifier=True,
            )
        ]
    assert exc_info.value.status_code == 503
    assert exc_info.value.failure_detail == "owner_input_shape_upgrade_required"


@pytest.mark.parametrize(
    ("input_value", "requires_upgrade"),
    [
        ([], False),
        ([{"role": "system", "content": "short"}], False),
        ([{"role": "developer", "content": "short"}], False),
        ([{"role": "system", "content": "x" * 4096}], True),
        ([{"role": "developer", "content": "x" * 4096}], True),
        ([{"role": "system", "content": "a"}, {"role": "developer", "content": "b"}], True),
    ],
    ids=["empty", "small-system", "small-developer", "large-system", "large-developer", "multi-instructions"],
)
def test_normalized_empty_classifier_disagreement(input_value: object, requires_upgrade: bool) -> None:
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": input_value})
    assert payload.input == []
    assert forwarding._http_bridge_owner_forward_requires_shape_upgrade(payload) is requires_upgrade


@pytest.mark.asyncio
@pytest.mark.parametrize("has_capability", [False, True], ids=["unsupported-owner", "missing-epoch"])
@pytest.mark.parametrize(
    "input_value",
    [
        [{"role": "system", "content": "x" * 4096}],
        [{"role": "developer", "content": "x" * 4096}],
        [{"role": "system", "content": "a"}, {"role": "developer", "content": "b"}],
    ],
    ids=["large-system", "large-developer", "multi-instructions"],
)
async def test_normalized_empty_disagreement_rejected_before_dispatch(
    monkeypatch: pytest.MonkeyPatch, has_capability: bool, input_value: object
) -> None:
    def unexpected_session(**_kwargs: object) -> None:
        pytest.fail("Classifier disagreement must fail before HTTP session creation")

    def unexpected_dispatch() -> None:
        pytest.fail("Rejected classifier disagreement must not mark the request dispatched")

    monkeypatch.setattr(forwarding.aiohttp, "ClientSession", unexpected_session)
    context = replace(_context(), expected_owner_process_epoch=None) if has_capability else _context()
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": input_value})
    assert payload.input == []
    with pytest.raises(ProxyResponseError) as exc_info:
        _ = [
            event
            async for event in forwarding.HTTPBridgeOwnerClient().stream_responses(
                owner_endpoint="http://owner",
                payload=payload,
                headers={},
                context=context,
                request_started_at=time.monotonic(),
                owner_supports_input_shape_classifier=has_capability,
                on_request_dispatched=unexpected_dispatch,
            )
        ]
    assert exc_info.value.status_code == 503
    assert exc_info.value.failure_detail == "owner_input_shape_upgrade_required"
