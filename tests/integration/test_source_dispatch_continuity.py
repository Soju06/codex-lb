from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.openai.requests import ResponsesRequest
from app.db.models import Account, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import api as proxy_api
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)
from tests.integration.test_http_responses_bridge import _install_bridge_settings
from tests.integration.test_model_source_dispatch import (
    _USAGE,
    _completed,
    _create_limited_key,
    _created,
    _drain,
    _reservations,
    _source_rows,
    _sse_handler,
    _StubState,
)
from tests.integration.test_proxy_responses import _disable_http_bridge, _make_auth_json  # noqa: F401

pytestmark = pytest.mark.integration
_PATHS = ["/backend-api/codex/responses", "/v1/responses"]
_StartSource = Callable[[Callable[[web.Request], Awaitable[web.StreamResponse]]], Awaitable[str]]


@pytest.fixture
async def source_upstream() -> AsyncIterator[_StartSource]:
    async with stub_source_upstreams() as start:
        yield start


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("bridge_enabled", [False, True])
@pytest.mark.parametrize(
    ("previous_response_id", "turn_state"),
    [
        (None, None),
        ("resp_0123456789abcdef0123456789abcdef", None),
        (None, "turn_unregistered_source"),
        (None, "http_turn_unregistered_source"),
    ],
)
async def test_source_owned_request_constructs_one_dispatch_and_settles(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    source_upstream: _StartSource,
    path: str,
    previous_response_id: str | None,
    turn_state: str | None,
    bridge_enabled: bool,
) -> None:
    _install_bridge_settings(monkeypatch, enabled=bridge_enabled)
    await _enable_api_key_auth(async_client)
    state = _StubState()
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created(), _completed(_USAGE)]))
    model = "dispatch-continuity-source"
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    key, key_id = await _create_limited_key(async_client, source_id, name="continuity-key")
    dispatch = Mock(wraps=proxy_api.SourceDispatch)
    claim_source = Mock(wraps=proxy_api.try_claim_source_admission)
    monkeypatch.setattr(proxy_api, "SourceDispatch", dispatch)
    monkeypatch.setattr(proxy_api, "try_claim_source_admission", claim_source)
    settle = AsyncMock(wraps=proxy_api._settle_source_reservation)
    monkeypatch.setattr(proxy_api, "_settle_source_reservation", settle)
    service = get_proxy_service_for_app(app_instance)
    candidates = AsyncMock(wraps=service._load_balancer.list_continuity_owner_candidates)
    subscription = Mock(wraps=proxy_module.core_stream_responses)
    websocket = AsyncMock(wraps=proxy_module.connect_responses_websocket)
    monkeypatch.setattr(service._load_balancer, "list_continuity_owner_candidates", candidates)
    monkeypatch.setattr(proxy_module, "core_stream_responses", subscription)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", websocket)
    payload = {"model": model, "input": "continue", "stream": True}
    if previous_response_id is not None:
        payload["previous_response_id"] = previous_response_id
    headers = {"authorization": f"Bearer {key}"}
    if turn_state is not None:
        headers["x-codex-turn-state"] = turn_state
    stream = _AsgiStream(
        app=app_instance,
        path=path,
        headers=headers,
        body=json.dumps(payload).encode(),
    )

    await asyncio.wait_for(stream.run(), timeout=10)
    await _drain(async_client)

    assert stream.status == 200
    assert b'"response.completed"' in stream.received()
    dispatch.assert_called_once()
    claim_source.assert_called_once()
    settle.assert_awaited_once()
    candidates.assert_not_called()
    subscription.assert_not_called()
    websocket.assert_not_called()
    assert len(state.requests) == 1
    assert state.requests[0].get("previous_response_id") == previous_response_id
    assert [reservation.status for reservation in await _reservations(key_id)] == ["finalized"]
    rows = await _source_rows(source_id)
    assert len(rows) == 1
    assert rows[0].request_id == "resp_dispatch_1"
    assert rows[0].status == "success"
    assert rows[0].account_id is None
    assert rows[0].api_key_id == key_id
    assert get_source_bulkhead().in_flight(source_id) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PATHS)
async def test_recorded_subscription_owner_never_constructs_source_dispatch(
    async_client: AsyncClient,
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    source_upstream: _StartSource,
    path: str,
) -> None:
    model = "dispatch-continuity-overlap"
    state = _StubState()
    base_url = await source_upstream(_sse_handler(state, before_hold=[_created(), _completed(_USAGE)]))
    source_id = await _create_model_source(
        async_client, name=model, model=model, base_url=base_url, supports_responses=True
    )
    raw_account_id = "acc_source_dispatch_owner"
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(_make_auth_json(raw_account_id, "owner@example.com")))},
    )
    assert imported.status_code == 200
    async with SessionLocal() as session:
        owner = (await session.scalars(select(Account).where(Account.chatgpt_account_id == raw_account_id))).one()
        session.add(
            RequestLog(
                account_id=owner.id,
                request_id="resp_owned_anchor",
                request_kind="response_create",
                model=model,
                status="success",
            )
        )
        await session.commit()
        await session.refresh(owner)
        session.expunge(owner)
    dispatch = Mock(wraps=proxy_api.SourceDispatch)
    claim_source = Mock(wraps=proxy_api.try_claim_source_admission)
    monkeypatch.setattr(proxy_api, "SourceDispatch", dispatch)
    monkeypatch.setattr(proxy_api, "try_claim_source_admission", claim_source)
    sent: list[tuple[str | None, str | None]] = []

    async def select_account(
        self: proxy_module.ProxyService, deadline: float, **kwargs: object
    ) -> proxy_module.AccountSelection:
        assert kwargs["preferred_account_id"] == owner.id
        return proxy_module.AccountSelection(account=owner, error_message=None, error_code=None)

    async def ensure_fresh(self: proxy_module.ProxyService, account: Account, **kwargs: object) -> Account:
        return account

    async def stream_subscription(
        payload: ResponsesRequest,
        headers: Mapping[str, str],
        access_token: str,
        account_id: str | None,
        **kwargs: object,
    ) -> AsyncIterator[str]:
        sent.append((account_id, payload.previous_response_id))
        yield _completed(_USAGE, "resp_subscription_followup").decode()

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget_compatible", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_stream_responses", stream_subscription)
    stream = _AsgiStream(
        app=app_instance,
        path=path,
        headers={},
        body=json.dumps(
            {"model": model, "input": "continue", "stream": True, "previous_response_id": "resp_owned_anchor"}
        ).encode(),
    )

    await asyncio.wait_for(stream.run(), timeout=10)
    await _drain(async_client)

    assert stream.status == 200
    assert b"resp_subscription_followup" in stream.received()
    assert sent == [(raw_account_id, "resp_owned_anchor")]
    dispatch.assert_not_called()
    claim_source.assert_not_called()
    assert state.requests == []
    assert await _source_rows(source_id) == []
    assert get_source_bulkhead().in_flight(source_id) == 0
