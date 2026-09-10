from __future__ import annotations

import json
from typing import Any, Literal, Never

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select

from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.db.models import Account, ApiKeyUsageReservation, DashboardSettings
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from tests.integration.test_http_promotion_accounting import _key
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as cleanup_http_bridge_sessions,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import _promotion_history, _PromotionUpstreamWebSocket
from tests.integration.test_http_responses_bridge import promotion_transport as promotion_transport
from tests.integration.test_owner_miss_catalog_cardinality import (
    PREVIOUS_RESPONSE_ID,
    _setup_catalog_scope,
)

pytestmark = pytest.mark.integration

# The shared promotion fixture stubs selection. These ownership tests need the
# real selector, including assignment scope, exclusions, and required owners.
_REAL_SELECT_ACCOUNT = proxy_module.ProxyService._select_account_with_budget
type PromotionTransport = tuple[list[_PromotionUpstreamWebSocket], list[dict[str, Any]], DashboardSettings]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("case", ["both-supported", "scoped-single"])
async def test_promoted_history_unknown_owner_preserves_assignment_cardinality(
    async_client: AsyncClient,
    app_instance: FastAPI,
    promotion_transport: PromotionTransport,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    case: Literal["both-supported", "scoped-single"],
) -> None:
    upstreams, raw_calls, _ = promotion_transport
    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", _REAL_SELECT_ACCOUNT)
    headers = await _setup_catalog_scope(async_client, app_instance, case)
    connect = proxy_module.connect_responses_websocket
    connected_accounts: list[str | None] = []

    async def observe_connect(
        headers: dict[str, str], access_token: str, account_id_header: str | None, **kwargs: Any
    ) -> UpstreamWebSocket:
        connected_accounts.append(account_id_header)
        return await connect(headers, access_token, account_id_header, **kwargs)

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", observe_connect)
    history = _promotion_history()
    response = await async_client.post(
        route,
        headers=headers,
        json={
            "model": "gpt-5.4",
            "instructions": "continue with the complete history",
            "input": history,
            "previous_response_id": PREVIOUS_RESPONSE_ID,
            "stream": True,
        },
    )

    assert raw_calls == []
    if case == "both-supported":
        assert "previous_response_owner_unavailable" in response.text, response.text
        assert upstreams == []
        assert connected_accounts == []
    else:
        assert response.status_code == 200, response.text
        assert "response.completed" in response.text
        assert len(upstreams) == 1
        assert connected_accounts == ["catalog_owner_1"]
        assert len(upstreams[0].sent_text) == 1
        frame = json.loads(upstreams[0].sent_text[0])
        assert frame["previous_response_id"] == PREVIOUS_RESPONSE_ID
        # The normal Responses adapter represents assistant text as output_text.
        assert frame["input"] == [
            history[0],
            {"role": "assistant", "content": [{"type": "output_text", "text": "first answer"}]},
            history[2],
        ]


@pytest.mark.asyncio
async def test_promoted_chat_predispatch_backoff_observes_settled_reservation(
    async_client: AsyncClient,
    app_instance: FastAPI,
    promotion_transport: PromotionTransport,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstreams, raw_calls, _ = promotion_transport
    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", _REAL_SELECT_ACCOUNT)
    key = await _key(async_client, "promotion-predispatch-backoff")
    service = get_proxy_service_for_app(app_instance)
    connected_accounts: list[str | None] = []
    backoff_reservation_states: list[list[str]] = []
    original_backoff = service._load_balancer.record_error_backoff

    async def reject_connect(
        headers: dict[str, str], access_token: str, account_id_header: str | None, **kwargs: Any
    ) -> Never:
        connected_accounts.append(account_id_header)
        raise ProxyResponseError(
            502,
            {"error": {"code": "upstream_unavailable", "message": "proxy connection failed"}},
            failure_phase="connect",
            retryable_same_contract=True,
            failure_detail="proxy_connect_pre_dispatch",
            failure_exception_type="ClientProxyConnectionError",
        )

    async def observe_backoff(account: Account) -> None:
        async with SessionLocal() as session:
            rows = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
            backoff_reservation_states.append([row.status for row in rows])
        await original_backoff(account)

    monkeypatch.setattr(proxy_module, "connect_responses_websocket", reject_connect)
    monkeypatch.setattr(service._load_balancer, "record_error_backoff", observe_backoff)
    response = await async_client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {key['key']}"},
        json={"model": "gpt-5.4", "messages": _promotion_history()},
    )

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "upstream_unavailable"
    await service.drain_persistence_tasks(timeout_seconds=5)
    assert connected_accounts == ["acc_promotion"]
    assert upstreams == []
    assert raw_calls == []
    assert backoff_reservation_states == [["released"]]
    async with SessionLocal() as session:
        rows = (await session.execute(select(ApiKeyUsageReservation))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == "released"
