from __future__ import annotations

import json
import threading
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

import app.modules.proxy.service as proxy_module
from app.db.models import Account, ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from tests.integration.test_openai_compat_features import _make_auth_json
from tests.integration.test_proxy_websocket_responses import (
    _FakeUpstreamMessage,
    _SequencedUpstreamWebSocket,
    _websocket_response_batch,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(20)]

_ANCHOR = "resp_c5ca4bbf04a26678c9ec342f00fe90fe69f3940780f7556092"
_PATHS = ["/backend-api/codex/responses", "/v1/responses"]


@pytest.fixture
def source_and_subscription_owner(app_instance):
    with TestClient(app_instance, base_url="http://localhost", client=("127.0.0.1", 50000)) as client:
        imported = client.post(
            "/api/accounts/import",
            files={
                "auth_json": (
                    "auth.json",
                    json.dumps(_make_auth_json("astra-owner", "owner@example.com")),
                    "application/json",
                )
            },
        )
        assert imported.status_code == 200
        source = client.post(
            "/api/model-sources/",
            json={
                "name": "astra-owner-schema",
                "baseUrl": "https://source.invalid/v1",
                "apiKey": "source-token",
                "supportsResponses": True,
                "models": [
                    {
                        "model": "gpt-6-astra",
                        "displayName": "Astra",
                        "contextWindow": 8192,
                        "maxOutputTokens": 1024,
                        "supportsStreaming": True,
                        "supportsTools": True,
                    }
                ],
            },
        )
        assert source.status_code == 200
        settings = client.put("/api/settings", json={"apiKeyAuthEnabled": True, "stickyThreadsEnabled": False})
        assert settings.status_code == 200
        created = client.post(
            "/api/api-keys/",
            json={
                "name": "astra-owner",
                "assignedSourceIds": [source.json()["id"]],
                "limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000}],
            },
        )
        assert created.status_code == 200
        key = created.json()

        async def record_owner():
            async with SessionLocal() as session:
                account = (await session.execute(select(Account))).scalar_one()
                session.add(
                    RequestLog(
                        account_id=account.id,
                        api_key_id=key["id"],
                        request_id=_ANCHOR,
                        model="gpt-6-astra",
                        status="success",
                    )
                )
                await session.commit()
                return account.id

        assert client.portal is not None
        account_id = client.portal.call(record_owner)
        yield client, key, account_id


async def _reservation_statuses(app):
    await app.state.proxy_service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as session:
        return list((await session.execute(select(ApiKeyUsageReservation.status))).scalars())


def _continuation(extra):
    return {
        "type": "response.create",
        "model": "gpt-6-astra",
        "instructions": "",
        "previous_response_id": _ANCHOR,
        "reasoning": {"effort": "low"},
        "input": [{"role": "user", "content": "Continue"}],
        **extra,
    }


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("transport", ["websocket", "http"])
@pytest.mark.parametrize(
    ("extra", "param"),
    [
        pytest.param({"top_logprobs": 2}, "top_logprobs", id="source-control"),
        pytest.param(
            {"input": [{"type": "configuration_update", "reasoning": {"effort": "low"}, "vendor_setting": True}]},
            "input.0",
            id="source-update",
        ),
        pytest.param(
            {"input": [{"type": "configuration_update", "reasoning": {"effort": 7}}]},
            "input.0.reasoning.effort",
            id="malformed-update",
        ),
    ],
)
def test_subscription_owner_rejects_source_schema(
    app_instance, source_and_subscription_owner, monkeypatch, path, transport, extra, param
):
    # Given an actual configured source and a durable subscription response owner.
    client, key, _ = source_and_subscription_owner
    connect = AsyncMock(side_effect=AssertionError("Invalid subscription schema reached upstream connect"))
    reserve = AsyncMock(wraps=proxy_module.ProxyService._reserve_websocket_api_key_usage)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)

    async def fail_stream(*args, **kwargs):
        raise AssertionError("Invalid subscription schema reached HTTP upstream")
        yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_stream)

    # Observe admission without replacing the real reservation implementation.
    async def record_reserve(self, *args, **kwargs):
        return await reserve(self, *args, **kwargs)

    monkeypatch.setattr(proxy_module.ProxyService, "_reserve_websocket_api_key_usage", record_reserve)

    # When the continuation arrives through the actual Responses route.
    headers = {"Authorization": "Bearer " + key["key"]}
    if transport == "websocket":
        with client.websocket_connect(path, headers=headers) as ws:
            ws.send_json(_continuation(extra))
            event = ws.receive_json()
        assert event["type"] == "error"
        assert event["status"] == 400, event
    else:
        response = client.post(path, headers=headers, json=_continuation(extra))
        assert response.status_code == 400, response.text
        event = response.json()

    # Then subscription validation rejects before admission or upstream work.
    assert event["error"]["type"] == "invalid_request_error"
    assert event["error"]["param"] == param
    connect.assert_not_awaited()
    reserve.assert_not_awaited()
    assert client.portal.call(_reservation_statuses, app_instance) == []


@pytest.mark.parametrize("path", _PATHS)
def test_websocket_source_without_subscription_owner_keeps_http_fallback(
    app_instance, source_and_subscription_owner, monkeypatch, path
):
    client, key, _ = source_and_subscription_owner
    connect = AsyncMock(side_effect=AssertionError("Source-only request reached subscription connect"))
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    payload = _continuation({"previous_response_id": "resp_source_unrecorded", "top_logprobs": 2})

    with client.websocket_connect(path, headers={"Authorization": "Bearer " + key["key"]}) as ws:
        ws.send_json(payload)
        event = ws.receive_json()

    assert event["type"] == "error"
    assert event["status"] == 503
    assert event["error"]["code"] == "model_source_requires_http_transport"
    connect.assert_not_awaited()
    assert client.portal.call(_reservation_statuses, app_instance) == ["released"]

    async def failure_outcome():
        async with SessionLocal() as session:
            return (
                await session.execute(
                    select(RequestLog.status, RequestLog.error_code).where(RequestLog.request_id != _ANCHOR)
                )
            ).one()

    assert client.portal.call(failure_outcome) == ("error", "model_source_requires_http_transport")


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("effort, wire_effort", [("ultra", "max"), ("low", "low")])
def test_websocket_subscription_owner_preserves_valid_update(
    app_instance, source_and_subscription_owner, monkeypatch, path, effort, wire_effort
):
    client, key, account_id = source_and_subscription_owner
    updated = client.patch("/api/api-keys/" + key["id"], json={"allowedReasoningEfforts": ["low", "ultra"]})
    assert updated.status_code == 200
    messages = _websocket_response_batch("resp_astra_owner_completed")
    messages[-1].text = json.dumps(
        {
            "type": "response.completed",
            "response": {
                "id": "resp_astra_owner_completed",
                "status": "completed",
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            },
        }
    )
    upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=[messages])
    finalized = threading.Event()
    original_finalize = proxy_module.ProxyService._finalize_websocket_request_state

    async def record_finalize(self, *args, **kwargs):
        await original_finalize(self, *args, **kwargs)
        finalized.set()

    monkeypatch.setattr(proxy_module.ProxyService, "_finalize_websocket_request_state", record_finalize)
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    update = {"type": "configuration_update", "reasoning": {"effort": effort}}

    with client.websocket_connect(path, headers={"Authorization": "Bearer " + key["key"]}) as ws:
        ws.send_json(_continuation({"input": [update, {"role": "user", "content": "Continue"}]}))
        assert ws.receive_json()["type"] == "response.created"
        assert ws.receive_json()["type"] == "response.completed"
        assert finalized.wait(timeout=5)

    connect.assert_awaited_once()
    assert connect.call_args.args[2] == "astra-owner"
    forwarded = json.loads(upstream.sent_text[0])
    assert forwarded["previous_response_id"] == _ANCHOR
    assert forwarded["reasoning"] == {"effort": "low"}
    assert forwarded["input"][0] == {"type": "configuration_update", "reasoning": {"effort": wire_effort}}
    assert client.portal.call(_reservation_statuses, app_instance) == ["finalized"]

    async def completed_owner():
        async with SessionLocal() as session:
            return (
                await session.execute(
                    select(RequestLog.account_id).where(RequestLog.request_id == "resp_astra_owner_completed")
                )
            ).scalar_one()

    assert client.portal.call(completed_owner) == account_id


@pytest.mark.parametrize("path", _PATHS)
def test_websocket_trailing_slash_keeps_existing_close(source_and_subscription_owner, path):
    client, key, _ = source_and_subscription_owner
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path + "/", headers={"Authorization": "Bearer " + key["key"]}):
            pytest.fail("The existing WebSocket route does not accept a trailing slash")
    assert exc.value.code == 1000


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("anchor", [_ANCHOR, "resp_source_unrecorded"], ids=["subscription", "source"])
@pytest.mark.parametrize("policy", [{"allowedReasoningEfforts": ["low"]}, {"enforcedReasoningEffort": "low"}])
def test_websocket_owner_selection_preserves_key_policy(
    app_instance, source_and_subscription_owner, monkeypatch, path, anchor, policy
):
    client, key, _ = source_and_subscription_owner
    updated = client.patch("/api/api-keys/" + key["id"], json=policy)
    assert updated.status_code == 200
    connect = AsyncMock(side_effect=AssertionError("Unauthorized effort reached upstream"))
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    payload = _continuation(
        {
            "previous_response_id": anchor,
            "input": [{"type": "configuration_update", "reasoning": {"effort": "high"}}],
        }
    )

    with client.websocket_connect(path, headers={"Authorization": "Bearer " + key["key"]}) as ws:
        ws.send_json(payload)
        event = ws.receive_json()

    assert event["status"] == 403
    assert event["error"]["code"] == "reasoning_effort_not_allowed"
    assert event["error"]["param"] == "input.0.reasoning.effort"
    connect.assert_not_awaited()
    assert client.portal.call(_reservation_statuses, app_instance) == []


def test_websocket_subscription_owner_validates_proxy_injected_full_resend_before_fallback(
    app_instance, source_and_subscription_owner, monkeypatch
):
    client, key, _ = source_and_subscription_owner
    updated = client.patch("/api/api-keys/" + key["id"], json={"allowedReasoningEfforts": ["low"]})
    assert updated.status_code == 200
    connect = AsyncMock(side_effect=AssertionError("Invalid full resend reached upstream connect"))
    reserve = AsyncMock(wraps=proxy_module.ProxyService._reserve_websocket_api_key_usage)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)

    async def record_reserve(self, *args, **kwargs):
        return await reserve(self, *args, **kwargs)

    monkeypatch.setattr(proxy_module.ProxyService, "_reserve_websocket_api_key_usage", record_reserve)
    session_id = "sid-astra-owner-full-resend"
    historical_update = {"type": "configuration_update", "reasoning": {"effort": "high"}}
    proxy_service = get_proxy_service_for_app(app_instance)
    proxy_service._websocket_continuity_index[(session_id, key["id"])] = proxy_module._WebSocketContinuityState(
        last_completed_response_id=_ANCHOR,
        last_completed_input_count=1,
        last_completed_input_prefix_fingerprint=proxy_module._fingerprint_input_items([historical_update]),
    )

    with client.websocket_connect(
        "/backend-api/codex/responses",
        headers={"Authorization": "Bearer " + key["key"], "session_id": session_id},
    ) as ws:
        ws.send_json(
            {
                "type": "response.create",
                "model": "gpt-6-astra",
                "instructions": "",
                "reasoning": {"effort": "low"},
                "input": [historical_update, {"role": "user", "content": "Continue"}],
            }
        )
        event = ws.receive_json()

    assert event["status"] == 403
    assert event["error"]["code"] == "reasoning_effort_not_allowed"
    assert event["error"]["param"] == "input.0.reasoning.effort"
    connect.assert_not_awaited()
    reserve.assert_not_awaited()
    assert client.portal.call(_reservation_statuses, app_instance) == []


def test_websocket_subscription_owner_replays_validated_proxy_injected_full_resend(
    app_instance, source_and_subscription_owner, monkeypatch
):
    client, key, _ = source_and_subscription_owner
    updated = client.patch("/api/api-keys/" + key["id"], json={"allowedReasoningEfforts": ["ultra"]})
    assert updated.status_code == 200
    stale_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "error": {
                                "type": "invalid_request_error",
                                "code": "previous_response_not_found",
                                "message": "Invalid previous_response_id.",
                                "param": "previous_response_id",
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            ]
        ],
    )
    replay_upstream = _SequencedUpstreamWebSocket(
        [],
        deferred_message_batches=[_websocket_response_batch("resp_astra_owner_replayed")],
    )
    connect = AsyncMock(side_effect=[stale_upstream, replay_upstream])
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    session_id = "sid-astra-owner-valid-replay"
    historical_update = {"type": "configuration_update", "reasoning": {"effort": "ultra"}}
    proxy_service = get_proxy_service_for_app(app_instance)
    proxy_service._websocket_continuity_index[(session_id, key["id"])] = proxy_module._WebSocketContinuityState(
        last_completed_response_id=_ANCHOR,
        last_completed_input_count=1,
        last_completed_input_prefix_fingerprint=proxy_module._fingerprint_input_items([historical_update]),
    )
    full_input = [historical_update, {"role": "user", "content": "Continue"}]

    with client.websocket_connect(
        "/backend-api/codex/responses",
        headers={"Authorization": "Bearer " + key["key"], "session_id": session_id},
    ) as ws:
        ws.send_json(
            {
                "type": "response.create",
                "model": "gpt-6-astra",
                "instructions": "",
                "reasoning": {"effort": "ultra"},
                "input": full_input,
            }
        )
        assert ws.receive_json()["type"] == "response.created"
        assert ws.receive_json()["type"] == "response.completed"

    assert connect.await_count == 2
    selected = json.loads(stale_upstream.sent_text[0])
    assert selected["previous_response_id"] == _ANCHOR
    assert selected["input"] == [
        {"type": "configuration_update", "reasoning": {"effort": "max"}},
        full_input[1],
    ]
    replay = json.loads(replay_upstream.sent_text[0])
    assert "previous_response_id" not in replay
    assert replay["input"] == [
        {"type": "configuration_update", "reasoning": {"effort": "max"}},
        full_input[1],
    ]
