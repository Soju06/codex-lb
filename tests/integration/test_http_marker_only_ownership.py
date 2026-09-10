from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.db.models import ApiKey
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.durable_bridge_runtime import http_bridge_owner_process_epoch
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as cleanup_http_bridge_sessions,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import _FakeBridgeUpstreamWebSocket, _install_bridge_settings
from tests.integration.test_proxy_compact_marker_cardinality import _scoped_key

pytestmark = pytest.mark.integration

ROUTES = [
    "/backend-api/codex/responses",
    "/backend-api/codex/responses/",
    "/v1/responses",
    "/v1/responses/",
]
MARKERS = ["turn_unregistered_http", "http_turn_unregistered_http"]


def _install_upstream(
    monkeypatch: pytest.MonkeyPatch, *, bridge: bool
) -> tuple[_FakeBridgeUpstreamWebSocket, list[str | None]]:
    _install_bridge_settings(monkeypatch, enabled=bridge)
    upstream = _FakeBridgeUpstreamWebSocket()
    dispatched: list[str | None] = []

    async def connect(
        headers: Mapping[str, str], access_token: str, account_id_header: str | None, **kwargs: Any
    ) -> _FakeBridgeUpstreamWebSocket:
        assert bridge
        dispatched.append(account_id_header)
        return upstream

    async def stream(
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        access_token: str,
        account_id: str | None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        assert not bridge
        assert payload.previous_response_id is None
        dispatched.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_marker_http",'
            '"object":"response","status":"completed","output":[]}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    return upstream, dispatched


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("bridge", [False, True], ids=["raw", "bridge"])
@pytest.mark.parametrize("candidate_count", [0, 1, 2])
async def test_marker_only_http_requires_sole_scoped_owner(
    async_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    marker: str,
    bridge: bool,
    candidate_count: int,
) -> None:
    key, _ = await _scoped_key(async_client, candidate_count)
    upstream, dispatched = _install_upstream(monkeypatch, bridge=bridge)
    response = await async_client.post(
        route,
        headers={"Authorization": f"Bearer {key}", "x-codex-turn-state": marker},
        json={
            "model": "gpt-5.1",
            "instructions": "continue",
            # Opaque state exercises sole-owner fallback; plain text is stateless.
            "input": [{"type": "reasoning", "encrypted_content": "opaque-test-state", "summary": []}],
            "stream": True,
        },
    )

    if candidate_count == 1:
        assert response.status_code == 200, response.text
        assert "response.completed" in response.text
        assert dispatched == ["acc_marker_cardinality_0"]
        assert len(upstream.sent_text) == int(bridge)
    else:
        assert response.status_code == 502, response.text
        assert response.json()["error"] == {
            "code": "previous_response_owner_unavailable",
            "message": "Previous response owner account is unavailable; retry later.",
            "type": "server_error",
        }
        assert dispatched == []
        assert upstream.sent_text == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("bridge", [False, True], ids=["raw", "bridge"])
@pytest.mark.parametrize("owner", ["registered", "file", "fresh"])
async def test_marker_only_http_preserves_independent_owners_and_first_turns(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    bridge: bool,
    owner: str,
) -> None:
    key, account_ids = await _scoped_key(async_client, 2)
    upstream, dispatched = _install_upstream(monkeypatch, bridge=bridge)
    service = get_proxy_service_for_app(app_instance)
    candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
    monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
    headers = {"Authorization": f"Bearer {key}"}
    payload: dict[str, JsonValue] = {"model": "gpt-5.1", "input": "next", "stream": True}
    if owner != "fresh":
        headers["x-codex-turn-state"] = MARKERS[0]
    if owner == "registered":
        async with SessionLocal() as session:
            api_key = (await session.scalars(select(ApiKey))).one()
            key_id = api_key.id
        claim = await service._durable_bridge.claim_live_session(
            session_key_kind="prompt_cache",
            session_key_value="registered-http-marker",
            api_key_id=key_id,
            instance_id="instance-a",
            owner_process_epoch=http_bridge_owner_process_epoch(),
            lease_ttl_seconds=60.0,
            account_id=account_ids[1],
            model="gpt-5.1",
            service_tier=None,
            latest_turn_state=MARKERS[0],
            latest_response_id=None,
            allow_takeover=True,
        )
        await service._durable_bridge.register_turn_state(
            session_id=claim.session_id,
            api_key_id=key_id,
            instance_id="instance-a",
            owner_epoch=claim.owner_epoch,
            turn_state=MARKERS[0],
            lease_ttl_seconds=60.0,
        )
    elif owner == "file":
        await service._pin_file_account("file_marker_http_owner", account_ids[1])
        payload["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": "file_marker_http_owner"}]}]

    response = await async_client.post(route, headers=headers, json=payload)

    assert response.status_code == 200, response.text
    assert "response.completed" in response.text
    assert len(dispatched) == 1
    if owner != "fresh":
        assert dispatched == ["acc_marker_cardinality_1"]
    assert len(upstream.sent_text) == int(bridge)
    candidates.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("bridge", [False, True], ids=["raw", "bridge"])
async def test_marker_only_http_keeps_live_registered_owner_without_durable_alias(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    bridge: bool,
) -> None:
    key, account_ids = await _scoped_key(async_client, 2)
    upstream, dispatched = _install_upstream(monkeypatch, bridge=True)
    service = get_proxy_service_for_app(app_instance)
    await service._pin_file_account("file_live_marker_owner", account_ids[1])
    headers = {"Authorization": f"Bearer {key}", "x-codex-turn-state": MARKERS[0]}
    bootstrap = await async_client.post(
        route,
        headers=headers,
        json={
            "model": "gpt-5.1",
            "stream": True,
            "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file_live_marker_owner"}]}],
        },
    )
    assert bootstrap.status_code == 200, bootstrap.text
    assert "response.completed" in bootstrap.text
    assert dispatched == ["acc_marker_cardinality_1"]
    await service.drain_persistence_tasks(timeout_seconds=5)
    monkeypatch.setattr(service._durable_bridge, "lookup_turn_state_target", AsyncMock(return_value=None))
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=None))
    candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
    monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
    if not bridge:
        upstream, dispatched = _install_upstream(monkeypatch, bridge=False)

    response = await async_client.post(
        route, headers=headers, json={"model": "gpt-5.1", "input": "next", "stream": True}
    )

    assert response.status_code == 200, response.text
    assert "response.completed" in response.text
    assert dispatched == ["acc_marker_cardinality_1"]
    assert len(upstream.sent_text) == (2 if bridge else 0)
    candidates.assert_not_awaited()
