from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.db.models import Account
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from tests.integration.test_model_source_routing import _create_model_source
from tests.integration.test_proxy_responses import _make_auth_json
from tests.integration.test_proxy_websocket_responses import (
    _SequencedUpstreamWebSocket,
    _stub_request_logging,  # noqa: F401
    _websocket_response_batch,
)

pytestmark = pytest.mark.integration

_MODEL = "registered-ws-source-overlap"


@pytest.fixture
def websocket_client(app_instance):
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        yield client


def _owner_on_portal(client: TestClient, app_instance, marker: str) -> Account:
    async def setup():
        async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as setup_client:
            return await _registered_owner(setup_client, app_instance, marker)

    assert client.portal is not None
    return client.portal.call(setup)


async def _registered_owner(async_client, app_instance, marker: str, *, api_key_id: str | None = None) -> Account:
    await _create_model_source(
        async_client,
        name="registered-ws-source",
        model=_MODEL,
        base_url="http://127.0.0.1:1/v1",
        supports_responses=True,
    )
    auth = _make_auth_json("acc_registered_ws_owner", "registered-ws@example.com")
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth), "application/json")},
    )
    assert imported.status_code == 200
    async with SessionLocal() as session:
        owner = (await session.scalars(select(Account))).one()
    service = get_proxy_service_for_app(app_instance)
    claim = await service._durable_bridge.claim_live_session(
        session_key_kind="prompt_cache",
        session_key_value="registered-ws-owner",
        api_key_id=api_key_id,
        instance_id="registered-ws-instance",
        owner_process_epoch="registered-ws-process",
        lease_ttl_seconds=60.0,
        account_id=owner.id,
        model=_MODEL,
        service_tier=None,
        latest_turn_state=marker,
        latest_response_id=None,
        allow_takeover=True,
    )
    await service._durable_bridge.register_turn_state(
        session_id=claim.session_id,
        api_key_id=api_key_id,
        instance_id="registered-ws-instance",
        owner_epoch=claim.owner_epoch,
        turn_state=marker,
        lease_ttl_seconds=60.0,
    )
    return owner


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("marker", ["turn_registered_ws_owner", "http_turn_registered_ws_owner"])
@pytest.mark.parametrize("reuse", [False, True], ids=["initial", "reused"])
@pytest.mark.parametrize("anchor", [None, "resp_missing_registered_ws_anchor"], ids=["no-anchor", "client-anchor"])
def test_registered_turn_owner_overrides_source_on_direct_websocket(
    websocket_client, app_instance, monkeypatch, path, marker, reuse, anchor
):
    owner = _owner_on_portal(websocket_client, app_instance, marker)
    batches = [_websocket_response_batch("resp_registered_ws_complete")]
    if reuse:
        batches.insert(0, _websocket_response_batch("resp_registered_ws_bootstrap"))
    upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=batches)
    selected: list[str | None] = []
    opened: list[str] = []

    async def select_owner(self, *args, preferred_account_id=None, **kwargs):
        selected.append(preferred_account_id)
        assert preferred_account_id == owner.id
        assert kwargs["require_preferred_account"] is True
        return owner

    async def open_owner(self, account, headers, **kwargs):
        opened.append(account.id)
        return account, upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", select_owner)
    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", open_owner)

    client = websocket_client
    with client.websocket_connect(f"ws://localhost{path}", headers={"x-codex-turn-state": marker}) as websocket:
        if reuse:
            websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "bootstrap"})
            assert websocket.receive_json()["type"] == "response.created"
            assert websocket.receive_json()["type"] == "response.completed"
        request = {"type": "response.create", "model": _MODEL, "input": "continue"}
        if anchor is not None:
            request["previous_response_id"] = anchor
        websocket.send_json(request)
        created = websocket.receive_json()
        assert created["type"] == "response.created", created
        assert websocket.receive_json()["type"] == "response.completed"

    assert selected == [owner.id]
    assert opened == [owner.id]
    sent = json.loads(upstream.sent_text[-1])
    assert sent["model"] == _MODEL
    if anchor is not None:
        assert sent["previous_response_id"] == anchor


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("marker", ["turn_registered_ws_owner", "http_turn_registered_ws_owner"])
@pytest.mark.parametrize("reuse", [False, True], ids=["initial", "reused"])
@pytest.mark.parametrize("conflicting_owner", ["previous-response", "file"])
def test_registered_turn_conflict_fails_before_source_guard_or_dispatch(
    websocket_client, app_instance, monkeypatch, path, marker, reuse, conflicting_owner
):
    owner = _owner_on_portal(websocket_client, app_instance, marker)
    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("resp_before_owner_conflict")]
    )
    selected = AsyncMock(return_value=owner)
    opened = AsyncMock(return_value=(owner, upstream))
    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", selected)
    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", opened)

    client = websocket_client
    with client.websocket_connect(f"ws://localhost{path}", headers={"x-codex-turn-state": marker}) as websocket:
        if reuse:
            websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "bootstrap"})
            assert websocket.receive_json()["type"] == "response.created"
            assert websocket.receive_json()["type"] == "response.completed"
        request: dict[str, object] = {
            "type": "response.create",
            "model": _MODEL,
            "input": "conflicting continuation",
        }
        if conflicting_owner == "previous-response":
            monkeypatch.setattr(
                proxy_module.ProxyService,
                "_resolve_websocket_previous_response_owner",
                AsyncMock(return_value="another-account"),
            )
            request["previous_response_id"] = "resp_conflicting_owner"
        else:
            monkeypatch.setattr(
                proxy_module.ProxyService,
                "_resolve_file_account_for_responses",
                AsyncMock(return_value="another-account"),
            )
            request["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": "file_ws_conflict"}]}]
        websocket.send_json(request)
        rejected = websocket.receive_json()
        assert rejected["type"] == "response.failed", rejected
        assert rejected["response"]["error"]["code"] == "continuity_owner_conflict"

    assert selected.await_count == int(reuse)
    assert opened.await_count == int(reuse)
    assert len(upstream.sent_text) == int(reuse)


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("marker", ["turn_scoped_ws_owner", "http_turn_scoped_ws_owner"])
@pytest.mark.parametrize("matching_scope", [False, True], ids=["foreign-key", "matching-key"])
def test_registered_turn_source_override_uses_request_api_key_scope(
    app_instance, monkeypatch, path, marker, matching_scope
):
    async def setup():
        async with SessionLocal() as session:
            keys = ApiKeysService(ApiKeysRepository(session))
            registered_key = await keys.create_key(ApiKeyCreateData(name="registered-ws", allowed_models=None))
            other_key = await keys.create_key(ApiKeyCreateData(name="other-ws", allowed_models=None))
        async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as setup_client:
            owner = await _registered_owner(setup_client, app_instance, marker, api_key_id=registered_key.id)
            settings = await setup_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
            assert settings.status_code == 200
        return owner, registered_key if matching_scope else other_key

    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("resp_scoped_ws_complete")]
    )
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        owner, request_key = client.portal.call(setup)
        selected = AsyncMock(return_value=owner)
        monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", selected)
        monkeypatch.setattr(
            proxy_module.ProxyService, "_try_open_websocket_connect_attempt", AsyncMock(return_value=(owner, upstream))
        )
        headers = {"x-codex-turn-state": marker, "Authorization": f"Bearer {request_key.key}"}
        with client.websocket_connect(f"ws://localhost{path}", headers=headers) as websocket:
            websocket.send_json({"type": "response.create", "model": _MODEL, "input": "scoped request"})
            event = websocket.receive_json()
            if matching_scope:
                assert event["type"] == "response.created", event
                assert websocket.receive_json()["type"] == "response.completed"
            else:
                assert event["type"] == "error", event
                assert event["status"] == 503
                assert event["error"]["code"] == "model_source_requires_http_transport"
    assert selected.await_count == int(matching_scope)
    assert len(upstream.sent_text) == int(matching_scope)
    if matching_scope:
        assert selected.await_args is not None
        assert selected.await_args.kwargs["preferred_account_id"] == owner.id
        assert selected.await_args.kwargs["require_preferred_account"] is True


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize(
    ("marker", "reuse"),
    [("turn_unregistered", False), ("http_turn_unregistered", False), (None, False), (None, True)],
)
def test_source_only_websocket_still_requires_http(websocket_client, app_instance, monkeypatch, path, marker, reuse):
    owner = _owner_on_portal(websocket_client, app_instance, "turn_unrelated_registered_owner")
    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("resp_source_only_bootstrap")]
    )
    selected = AsyncMock(return_value=owner)
    opened = AsyncMock(return_value=(owner, upstream))
    monkeypatch.setattr(proxy_module.ProxyService, "_select_websocket_connect_account", selected)
    monkeypatch.setattr(proxy_module.ProxyService, "_try_open_websocket_connect_attempt", opened)
    headers = {"x-codex-turn-state": marker} if marker is not None else {}
    client = websocket_client
    with client.websocket_connect(f"ws://localhost{path}", headers=headers) as websocket:
        if reuse:
            websocket.send_json({"type": "response.create", "model": "gpt-5.4", "input": "bootstrap"})
            assert websocket.receive_json()["type"] == "response.created"
            assert websocket.receive_json()["type"] == "response.completed"
        websocket.send_json({"type": "response.create", "model": _MODEL, "input": "source request"})
        rejected = websocket.receive_json()
        if reuse:
            assert rejected["type"] == "response.failed", rejected
            error = rejected["response"]["error"]
        else:
            assert rejected["type"] == "error", rejected
            assert rejected["status"] == 503
            error = rejected["error"]
        assert error["code"] == "model_source_requires_http_transport"

    assert selected.await_count == int(reuse)
    assert opened.await_count == int(reuse)
    assert len(upstream.sent_text) == int(reuse)
