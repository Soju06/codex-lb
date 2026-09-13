from __future__ import annotations

import json
from unittest.mock import AsyncMock

import anyio
import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL
from app.core.errors import openai_error
from app.core.openai.requests import ResponsesRequest
from app.db.models import ApiKeyLimit, ApiKeyUsageReservation, HttpBridgeOperationRecord, HttpBridgeSessionRecord
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service import support as transport_health
from app.modules.proxy._service.http_bridge import request_submit as bridge_request_submit
from app.modules.proxy._service.http_bridge import streaming as bridge_streaming
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_astra_inherited_policy import _reasoning_key
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as _cleanup_http_bridge_sessions,
)
from tests.integration.test_http_responses_bridge import (
    _ClosingBridgeUpstreamWebSocket,
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)
from tests.integration.test_openai_compat_features import _completed_event

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("path", "stream"),
    [("/v1/responses", False), ("/v1/responses", True), ("/backend-api/codex/responses", True)],
    ids=["v1-collect", "v1-stream", "backend-stream"],
)
@pytest.mark.parametrize("separator", ["none", "kept", "compact-fallback"])
async def test_http_bridge_validates_updates_after_replay_deduplication(
    async_client, monkeypatch, app_instance, path: str, stream: bool, separator: str
) -> None:
    keep_separator = separator == "kept"
    account_id = await _import_account(async_client, "astra-dedupe", "astra-dedupe@example.com")
    account = await _get_account(account_id)
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "astra-dedupe",
            "limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000}],
        },
    )
    assert created.status_code == 200
    key = created.json()
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _FakeBridgeUpstreamWebSocket()
    service = get_proxy_service_for_app(app_instance)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)

    async def fail_http_fallback(*args, **kwargs):
        raise AssertionError("Bridge continuation unexpectedly fell back to HTTP upstream")
        yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_http_fallback)
    registered = anyio.Event()
    original_register = service._register_http_bridge_previous_response_id

    async def register_response(session, response_id, **kwargs):
        result = await original_register(session, response_id, **kwargs)
        assert result
        registered.set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_response)
    headers = {"Authorization": "Bearer " + key["key"], "thread-id": "astra-dedupe-thread"}
    with anyio.fail_after(5):
        initial = await async_client.post(
            path,
            json={"model": "gpt-6-astra", "instructions": "", "input": [{"role": "user", "content": "Start"}]},
            headers=headers,
        )
        assert initial.status_code == 200, initial.text
        await registered.wait()
    assert len(upstream.sent_text) == 1
    anchor = "resp_bridge_1"
    call = {
        "type": "function_call",
        "name": "exec_command",
        "arguments": json.dumps({"cmd": "touch marker"}),
        "call_id": "call_replayed",
    }
    update = {"type": "configuration_update", "reasoning": {"effort": "low"}}
    items = [call, update, dict(call)]
    if keep_separator:
        items.append({"role": "assistant", "content": "Kept separator"})
    elif separator == "compact-fallback":
        items.append(
            {
                "type": "message",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Local compact fallback preserved the latest encrypted reasoning state.",
                    }
                ],
            }
        )
    items.extend([dict(update), {"role": "user", "content": "Continue"}])
    with anyio.fail_after(5):
        response = await async_client.post(
            path,
            json={
                "model": "gpt-6-astra",
                "instructions": "",
                "reasoning": {"effort": "low"},
                "previous_response_id": anchor,
                "input": items,
                "stream": stream,
            },
            headers=headers,
        )
    if keep_separator:
        assert response.status_code == 200, response.text
        if stream:
            events = [
                json.loads(line[6:])
                for line in response.text.splitlines()
                if line.startswith("data: ") and line != "data: [DONE]"
            ]
            assert sum(event.get("type") == "response.completed" for event in events) == 1
            assert not any(event.get("type") == "response.failed" for event in events)
        else:
            assert response.json()["status"] == "completed"
        assert len(upstream.sent_text) == 2
        forwarded = json.loads(upstream.sent_text[1])["input"]
        updates = [index for index, item in enumerate(forwarded) if item.get("type") == "configuration_update"]
        assert len(updates) == 2
        assert updates[1] > updates[0] + 1
        assert any("Kept separator" in json.dumps(item.get("content")) for item in forwarded)
        assert not any(item.get("type") == "function_call" for item in forwarded)
    else:
        assert response.status_code == 400, response.text
        assert response.json()["error"]["type"] == "invalid_request_error"
        assert response.json()["error"]["param"] == "input.2"
        assert len(upstream.sent_text) == 1
        connect.assert_awaited_once()
        await service.drain_persistence_tasks(timeout_seconds=5)
        async with SessionLocal() as db:
            statuses = list((await db.execute(select(ApiKeyUsageReservation.status))).scalars())
        assert sorted(statuses) == ["finalized", "released"]


@pytest.mark.parametrize("stream", [False, True], ids=["collect", "stream"])
async def test_astra_full_resend_preserves_bridge_prefix(async_client, monkeypatch, app_instance, stream: bool) -> None:
    # Given a real route, bridge and durable store, with only upstream I/O faked.
    account_id = await _import_account(async_client, "astra-history", "astra-history@example.com")
    account = await _get_account(account_id)
    key = await _reasoning_key(async_client, enforced="high")
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _FakeBridgeUpstreamWebSocket()
    service = get_proxy_service_for_app(app_instance)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", AsyncMock(return_value=upstream))
    registered = {f"resp_bridge_{turn}": anyio.Event() for turn in (1, 2, 3)}
    original_register = service._register_http_bridge_previous_response_id

    async def register_response(session, response_id, **kwargs):
        result = await original_register(session, response_id, **kwargs)
        assert result
        registered[response_id].set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_response)
    headers = {"Authorization": f"Bearer {key}", "thread-id": "astra-history-thread"}
    body = {
        "model": "gpt-6-astra",
        "instructions": "",
        "reasoning": {"effort": "high"},
        "stream": stream,
    }

    async def post_turn(input_items, response_id, *, previous_response_id=None):
        request_body = {**body, "input": input_items}
        if previous_response_id is not None:
            request_body["previous_response_id"] = previous_response_id
        with anyio.fail_after(5):
            response = await async_client.post("/v1/responses", json=request_body, headers=headers)
            assert response.status_code == 200, response.text
            if stream:
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ") and line != "data: [DONE]"
                ]
                assert any(
                    event.get("type") == "response.completed" and event["response"]["id"] == response_id
                    for event in events
                )
            else:
                assert response.json()["id"] == response_id
            await registered[response_id].wait()

    await post_turn([{"role": "user", "content": "First"}], "resp_bridge_1")
    history = [
        {"type": "message", "role": "assistant", "id": "msg_1", "status": "completed", "content": "done"},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        {"role": "user", "content": "Continue"},
    ]
    normalized_history = ResponsesRequest.model_validate({**body, "input": history}).input
    assert isinstance(normalized_history, list)

    # When a marked full resend is validated, trimmed, reset and completed.
    await post_turn(history, "resp_bridge_2", previous_response_id="resp_bridge_1")

    # Then durable bookkeeping describes client history, not reset + delta.
    async with SessionLocal() as db:
        stored = (
            await db.execute(
                select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.latest_response_id == "resp_bridge_2")
            )
        ).scalar_one()
        assert stored.latest_input_item_count == len(normalized_history)
        assert stored.latest_input_full_fingerprint == proxy_module._fingerprint_input_items(normalized_history)
    reset = {"type": "configuration_update", "reasoning": {"effort": "high"}}
    assert json.loads(upstream.sent_text[1])["input"] == [reset, *normalized_history[2:]]

    # A later unanchored full resend must match and reuse the completed anchor.
    await post_turn([*history, {"role": "user", "content": "Next"}], "resp_bridge_3")
    continuation = json.loads(upstream.sent_text[2])
    assert continuation["previous_response_id"] == "resp_bridge_2"
    assert continuation["input"] == [
        reset,
        {"role": "user", "content": "Next"},
    ]


@pytest.mark.parametrize(
    ("path", "stream"),
    [("/v1/responses", False), ("/v1/responses", True), ("/backend-api/codex/responses", True)],
    ids=["v1-collect", "v1-stream", "backend-stream"],
)
@pytest.mark.parametrize(
    ("effort", "enforced"), [("low", False), ("ultra", True)], ids=["allowed-low", "enforced-ultra"]
)
async def test_astra_late_ledger_anchor_preserves_client_prefix(
    async_client, monkeypatch, app_instance, path: str, stream: bool, effort: str, enforced: bool
) -> None:
    account_id = await _import_account(async_client, "astra-ledger", "astra-ledger@example.com")
    account = await _get_account(account_id)
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    policy = {"enforcedReasoningEffort": effort} if enforced else {"allowedReasoningEfforts": [effort]}
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "astra-ledger",
            "limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000}],
            **policy,
        },
    )
    assert created.status_code == 200
    key = created.json()
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _FakeBridgeUpstreamWebSocket()
    service = get_proxy_service_for_app(app_instance)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    late_anchors = []
    original_anchor = bridge_request_submit._text_with_previous_response_id

    def observe_anchor(text_data, response_id, **kwargs):
        updated = original_anchor(text_data, response_id, **kwargs)
        late_anchors.append(response_id)
        return updated

    monkeypatch.setattr(bridge_request_submit, "_text_with_previous_response_id", observe_anchor)
    registered = {f"resp_bridge_{turn}": anyio.Event() for turn in (1, 2, 3, 4)}
    original_register = service._register_http_bridge_previous_response_id

    async def register_response(session, response_id, **kwargs):
        result = await original_register(session, response_id, **kwargs)
        assert result
        registered[response_id].set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_response)
    headers = {"Authorization": "Bearer " + key["key"], "thread-id": "astra-ledger-thread"}
    body = {"model": "gpt-6-astra", "instructions": "", "reasoning": {"effort": effort}, "stream": stream}
    history = [{"role": "user", "content": "Continue"}]
    normalized_history = ResponsesRequest.model_validate({**body, "input": history}).input
    assert isinstance(normalized_history, list)

    async def post_turn(input_items, response_id, *, previous_response_id=None):
        request_body = {**body, "input": input_items}
        if previous_response_id is not None:
            request_body["previous_response_id"] = previous_response_id
        with anyio.fail_after(5):
            response = await async_client.post(path, json=request_body, headers=headers)
            assert response.status_code == 200, response.text
            if stream:
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
                completed = [event for event in events if event.get("type") == "response.completed"]
                assert len(completed) == 1, events
                assert completed[0]["response"]["id"] == response_id
                assert not any(event.get("type") in {"error", "response.failed"} for event in events)
            else:
                assert response.json()["id"] == response_id
            await registered[response_id].wait()
            return response

    # Current upstream journals anchored continuations, not unanchored hard turns.
    first = await post_turn(history, "resp_bridge_1")
    headers["x-codex-turn-state"] = first.headers["x-codex-turn-state"]
    await post_turn(history, "resp_bridge_2", previous_response_id="resp_bridge_1")
    async with SessionLocal() as db:
        operation = (
            await db.execute(
                select(HttpBridgeOperationRecord).where(HttpBridgeOperationRecord.response_id == "resp_bridge_2")
            )
        ).scalar_one()
        assert operation.state == "completed"
        assert operation.parent_response_id == "resp_bridge_1"

    # Model completion becoming visible between the initial lookup and the
    # completed-operation query. Only the two initial reads are stale; the
    # completion lookup, subsequent writes and history bookkeeping use real DBs.
    original_by_fingerprint = service._durable_bridge.get_operation_by_fingerprint
    original_by_id = service._durable_bridge.get_operation
    stale_reads = {"fingerprint", "id"}

    async def initial_fingerprint_miss(**kwargs):
        if "fingerprint" in stale_reads:
            stale_reads.remove("fingerprint")
            return None
        return await original_by_fingerprint(**kwargs)

    async def initial_id_miss(**kwargs):
        if "id" in stale_reads:
            stale_reads.remove("id")
            return None
        return await original_by_id(**kwargs)

    monkeypatch.setattr(service._durable_bridge, "get_operation_by_fingerprint", initial_fingerprint_miss)
    monkeypatch.setattr(service._durable_bridge, "get_operation", initial_id_miss)
    await post_turn(history, "resp_bridge_3", previous_response_id="resp_bridge_1")
    assert stale_reads == set()
    assert late_anchors == ["resp_bridge_2"]
    resets = (
        [{"type": "configuration_update", "reasoning": {"effort": "max" if effort == "ultra" else effort}}]
        if enforced
        else []
    )
    third = json.loads(upstream.sent_text[2])
    assert third["previous_response_id"] == "resp_bridge_2"
    assert third["input"] == [*resets, *normalized_history]
    async with SessionLocal() as db:
        stored = (
            await db.execute(
                select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.latest_response_id == "resp_bridge_3")
            )
        ).scalar_one()
        stored_count = stored.latest_input_item_count
        stored_fingerprint = stored.latest_input_full_fingerprint
    session = next(
        session
        for session in service._http_bridge_sessions.values()
        if session.last_completed_response_id == "resp_bridge_3"
    )
    live_count = session.last_completed_input_count
    live_fingerprint = session.last_completed_input_prefix_fingerprint
    assert stored_count == live_count == len(normalized_history)
    assert stored_fingerprint == live_fingerprint == proxy_module._fingerprint_input_items(normalized_history)

    # A client full resend omits the proxy reset and must still trim/reuse the third response.
    suffix = [
        {"role": "assistant", "content": [{"type": "output_text", "text": "OK"}]},
        {"role": "user", "content": "Next"},
    ]
    await post_turn([*history, *suffix], "resp_bridge_4")
    fourth = json.loads(upstream.sent_text[3])
    assert fourth.get("previous_response_id") == "resp_bridge_3"
    assert fourth["input"] == [*resets, *suffix]
    assert late_anchors == ["resp_bridge_2"]
    connect.assert_awaited_once()
    await service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as db:
        statuses = list((await db.execute(select(ApiKeyUsageReservation.status))).scalars())
        charged = await db.scalar(select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == key["id"]))
    assert statuses == ["finalized"] * 4
    assert charged == 104


@pytest.mark.parametrize("stream", [False, True], ids=["collect", "stream"])
@pytest.mark.parametrize("bridge_enabled", [False, True], ids=["disabled", "size-fallback"])
async def test_astra_full_resend_http_fallback_keeps_reset(
    async_client, monkeypatch, stream: bool, bridge_enabled: bool
) -> None:
    await _import_account(async_client, "astra-fallback", "astra-fallback@example.com")
    key = await _reasoning_key(async_client, enforced="high")
    _install_bridge_settings(monkeypatch, enabled=bridge_enabled)
    # Exercise the runtime size bypass after the route has chosen the bridge.
    monkeypatch.setattr(bridge_streaming, "_ws_transport_payload_budget_bytes", lambda max_sse_event_bytes=None: 1)
    forwarded = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_fallback")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    tool_output = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
    with anyio.fail_after(5):
        response = await async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-6-astra",
                "instructions": "",
                "reasoning": {"effort": "high"},
                "previous_response_id": "resp_stored",
                "stream": stream,
                "input": [
                    {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "shell", "arguments": "{}"},
                    tool_output,
                ],
            },
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.status_code == 200, response.text
    assert len(forwarded) == 1
    assert forwarded[0]["previous_response_id"] == "resp_stored"
    assert forwarded[0]["input"] == [
        {"type": "configuration_update", "reasoning": {"effort": "high"}},
        tool_output,
    ]


@pytest.mark.parametrize(
    ("path", "stream"),
    [("/v1/responses", False), ("/v1/responses", True), ("/backend-api/codex/responses", True)],
    ids=["v1-collect", "v1-stream", "backend-stream"],
)
@pytest.mark.parametrize("history", ["plain", "replay", "explicit"])
@pytest.mark.parametrize("key_mode", ["allowed", "enforced-ultra", "other-model-limit"])
async def test_astra_connect_fallback_preserves_prepared_continuation(
    async_client, monkeypatch, app_instance, path: str, stream: bool, history: str, key_mode: str
) -> None:
    account_id = await _import_account(async_client, "astra-connect-fallback", "astra-connect-fallback@example.com")
    account = await _get_account(account_id)
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    effort = "ultra" if key_mode == "enforced-ultra" else "low"
    key_body = {"name": "astra-connect-fallback"}
    if key_mode != "enforced-ultra":
        key_body["allowedReasoningEfforts"] = [effort]
    if key_mode == "enforced-ultra":
        key_body["enforcedReasoningEffort"] = effort
    if key_mode == "other-model-limit":
        key_body["limits"] = [
            {"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000, "modelFilter": "gpt-5.4"}
        ]
    created = await async_client.post("/api/api-keys/", json=key_body)
    assert created.status_code == 200, created.text
    key = created.json()
    _install_bridge_settings(monkeypatch, enabled=True)
    service = get_proxy_service_for_app(app_instance)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    first_upstream = _ClosingBridgeUpstreamWebSocket()
    connect = AsyncMock(
        side_effect=[
            first_upstream,
            ProxyResponseError(
                502,
                openai_error("upstream_unavailable", "Synthetic WebSocket connect failure", error_type="server_error"),
                failure_phase="connect",
                failure_detail=UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL,
            ),
        ],
    )
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    forwarded = []

    async def raw_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_connect_fallback")

    monkeypatch.setattr(proxy_module, "core_stream_responses", raw_stream)
    reset = {"type": "configuration_update", "reasoning": {"effort": effort}}
    user = {"role": "user", "content": "Continue"}
    tool_output = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
    input_items = [user]
    if history == "replay":
        input_items = [
            {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "shell", "arguments": "{}"},
            tool_output,
        ]
    elif history == "explicit":
        input_items = [reset, user]
    transport_health.clear_upstream_websocket_transport_failure()
    try:
        with anyio.fail_after(10):
            first = await async_client.post(
                "/v1/responses",
                json={
                    "model": "gpt-6-astra",
                    "instructions": "",
                    "input": "Hello",
                    "reasoning": {"effort": effort},
                    "prompt_cache_key": f"astra-fallback-{account_id}",
                },
                headers={"Authorization": f"Bearer {key['key']}"},
            )
        assert first.status_code == 200, first.text
        anchor = first.json()["id"]
        connect.assert_awaited_once()
        connect.reset_mock()
        assert not forwarded
        with anyio.fail_after(10):
            response = await async_client.post(
                path,
                json={
                    "model": "gpt-6-astra",
                    "instructions": "",
                    "reasoning": {"effort": effort},
                    "previous_response_id": anchor,
                    "prompt_cache_key": f"astra-fallback-{account_id}",
                    "input": input_items,
                    "stream": stream,
                },
                headers={"Authorization": f"Bearer {key['key']}"},
            )
        assert response.status_code == 200, response.text
        assert "resp_connect_fallback" in response.text
        connect.assert_awaited_once()
        assert len(forwarded) == 1
        sent = forwarded[0]
        wire_effort = "max" if effort == "ultra" else effort
        assert sent["previous_response_id"] == anchor
        assert sent["reasoning"] == {"effort": wire_effort}
        wire_reset = {"type": "configuration_update", "reasoning": {"effort": wire_effort}}
        normalized_user = {"role": "user", "content": "Continue"}
        expected_updates = [wire_reset] if key_mode == "enforced-ultra" or history == "explicit" else []
        assert sent["input"] == [*expected_updates, tool_output if history == "replay" else normalized_user]
        await service.drain_persistence_tasks(timeout_seconds=5)
        async with SessionLocal() as db:
            assert list((await db.execute(select(ApiKeyUsageReservation.status))).scalars()) == []
    finally:
        transport_health.clear_upstream_websocket_transport_failure()
