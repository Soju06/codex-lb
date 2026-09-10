from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Literal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import app.modules.proxy.service as proxy_module
from app.core.openai.model_registry import get_model_registry
from app.core.openai.models import CompactResponsePayload
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.types import JsonValue
from app.dependencies import get_proxy_service_for_app
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401
    _FakeBridgeUpstreamWebSocket,
    _import_account,
    _install_bridge_settings,
)
from tests.integration.test_proxy_websocket_responses import (
    _SequencedUpstreamWebSocket,
    _websocket_response_batch,
)
from tests.integration.test_v1_models import _make_upstream_model

pytestmark = pytest.mark.integration

CatalogCase = Literal["model-omission", "tier-omission", "both-supported", "scoped-single"]
Transport = Literal["stream", "bridge", "compact"]
ROUTES = ["/backend-api/codex/responses", "/v1/responses"]
CATALOG_CASES = ["model-omission", "tier-omission", "both-supported", "scoped-single"]
PREVIOUS_RESPONSE_ID = "resp_catalog_owner_not_recorded"


async def _setup_catalog_scope(client: AsyncClient, app: FastAPI, case: CatalogCase) -> dict[str, str]:
    account_ids = [
        await _import_account(client, f"catalog_owner_{index}", f"catalog-owner-{index}@example.com")
        for index in range(2)
    ]
    model = _make_upstream_model("gpt-5.4")
    other = _make_upstream_model("gpt-5.2")
    first_models = [model, other] if case == "both-supported" else [other]
    if case == "tier-omission":
        first_models = [
            _make_upstream_model(
                "gpt-5.4",
                raw={"service_tiers": [{"slug": "default"}], "additional_speed_tiers": []},
            ),
            other,
        ]
        model = _make_upstream_model(
            "gpt-5.4",
            raw={
                "service_tiers": [{"slug": "default"}, {"slug": "fast"}],
                "additional_speed_tiers": ["fast"],
            },
        )
    await get_model_registry().update(
        {"plus": [model, other]},
        per_account_results={
            account_ids[0]: ("plus", first_models),
            account_ids[1]: ("plus", [model, other]),
        },
        active_account_plans=dict.fromkeys(account_ids, "plus"),
    )
    settings = await client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    scoped_ids = account_ids[1:] if case == "scoped-single" else account_ids
    key = await client.post(
        "/api/api-keys/",
        json={
            "name": "catalog-owner-scope",
            "accountAssignmentScopeEnabled": True,
            "assignedAccountIds": scoped_ids,
        },
    )
    assert key.status_code == 200
    assert key.json()["accountAssignmentScopeEnabled"] is True
    assert set(key.json()["assignedAccountIds"]) == set(scoped_ids)

    service = get_proxy_service_for_app(app)
    owners = await service._load_balancer.list_continuity_owner_candidates(account_ids=scoped_ids)
    # Catalog eligibility may narrow routing, but cannot identify a historical owner.
    assert {account.id for account in owners} == set(scoped_ids)
    return {"Authorization": f"Bearer {key.json()['key']}"}


def _continuation_payload(case: CatalogCase) -> dict[str, JsonValue]:
    payload: dict[str, JsonValue] = {
        "model": "gpt-5.4",
        "instructions": "continue",
        "input": [],
        "previous_response_id": PREVIOUS_RESPONSE_ID,
    }
    if case == "tier-omission":
        payload["service_tier"] = "priority"
    return payload


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("transport", ["stream", "bridge", "compact"])
@pytest.mark.parametrize("case", CATALOG_CASES)
@pytest.mark.asyncio
async def test_http_owner_miss_ignores_catalog_cardinality(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    transport: Transport,
    case: CatalogCase,
) -> None:
    headers = await _setup_catalog_scope(async_client, app_instance, case)
    if transport == "bridge":
        _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _FakeBridgeUpstreamWebSocket()
    dispatched: list[str | None] = []

    async def connect(
        headers: Mapping[str, str],
        access_token: str,
        account_id_header: str | None,
        **kwargs: object,
    ) -> _FakeBridgeUpstreamWebSocket:
        dispatched.append(account_id_header)
        return upstream

    async def stream(
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        access_token: str,
        account_id: str | None,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        assert transport == "stream"
        dispatched.append(account_id)
        assert payload.previous_response_id == PREVIOUS_RESPONSE_ID
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_catalog_done",'
            '"object":"response","status":"completed","output":[]}}\n\n'
        )

    async def compact(
        payload: ResponsesCompactRequest,
        headers: Mapping[str, str],
        access_token: str,
        account_id: str | None,
        **kwargs: object,
    ) -> CompactResponsePayload:
        assert transport == "compact"
        dispatched.append(account_id)
        assert payload.model_dump()["previous_response_id"] == PREVIOUS_RESPONSE_ID
        return CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", compact)
    payload = _continuation_payload(case)
    if transport != "compact":
        payload["stream"] = True
    response = await async_client.post(
        route + ("/compact" if transport == "compact" else ""), headers=headers, json=payload
    )
    if case != "scoped-single":
        assert "previous_response_owner_unavailable" in response.text, response.text
        assert dispatched == []
        assert upstream.sent_text == []
    else:
        assert response.status_code == 200, response.text
        assert "response.completed" in response.text or "response.compaction" in response.text
        assert dispatched == ["catalog_owner_1"]
        if transport == "bridge":
            assert len(upstream.sent_text) == 1
            assert json.loads(upstream.sent_text[0])["previous_response_id"] == PREVIOUS_RESPONSE_ID


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("case", CATALOG_CASES)
def test_websocket_owner_miss_ignores_catalog_cardinality(
    app_instance: FastAPI, monkeypatch: pytest.MonkeyPatch, route: str, case: CatalogCase
) -> None:
    upstream = _SequencedUpstreamWebSocket(
        [], deferred_message_batches=[_websocket_response_batch("resp_catalog_ws_done")]
    )
    dispatched: list[str | None] = []

    async def connect(
        headers: Mapping[str, str],
        access_token: str,
        account_id_header: str | None,
        **kwargs: object,
    ) -> _SequencedUpstreamWebSocket:
        dispatched.append(account_id_header)
        return upstream

    async def setup() -> dict[str, str]:
        async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as client:
            return await _setup_catalog_scope(client, app_instance, case)

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    with TestClient(app_instance, client=("127.0.0.1", 50000)) as client:
        assert client.portal is not None
        headers = client.portal.call(setup)
        with client.websocket_connect(f"ws://localhost{route}", headers=headers) as websocket:
            payload = _continuation_payload(case)
            payload["type"] = "response.create"
            websocket.send_json(payload)
            event = websocket.receive_json()
            if case != "scoped-single":
                assert event["type"] == "response.failed", event
                assert event["response"]["error"]["code"] == "previous_response_owner_unavailable"
            else:
                assert event["type"] == "response.created", event
                assert websocket.receive_json()["type"] == "response.completed"
    assert dispatched == (["catalog_owner_1"] if case == "scoped-single" else [])
    assert len(upstream.sent_text) == int(case == "scoped-single")
    if upstream.sent_text:
        assert json.loads(upstream.sent_text[0])["previous_response_id"] == PREVIOUS_RESPONSE_ID
