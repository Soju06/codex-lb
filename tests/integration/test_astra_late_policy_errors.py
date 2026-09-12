from __future__ import annotations

import json
from unittest.mock import AsyncMock

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_module
from app.db.models import ApiKeyUsageReservation
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as _cleanup_http_bridge_sessions,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(20)]


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("enforced", [False, True], ids=["allowed-no-reset", "enforced-reset"])
@pytest.mark.parametrize(
    ("extra", "error_param"),
    [
        ({"truncation": "auto"}, "truncation"),
        ({"context_management": [{"type": "compaction", "compact_threshold": 1000}]}, "context_management"),
    ],
    ids=["auto-truncation", "auto-compaction"],
)
async def test_late_astra_anchor_policy_error_is_terminal_and_releases_reservation(
    async_client, app_instance, monkeypatch, path: str, enforced: bool, extra, error_param: str
) -> None:
    account_id = await _import_account(async_client, "astra-late", "astra-late@example.com")
    account = await _get_account(account_id)
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    policy = {"enforcedReasoningEffort": "low"} if enforced else {"allowedReasoningEfforts": ["low"]}
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "astra-late-policy",
            **policy,
            "limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000}],
        },
    )
    assert created.status_code == 200
    key = created.json()
    _install_bridge_settings(monkeypatch, enabled=True)
    service = get_proxy_service_for_app(app_instance)
    upstream = _FakeBridgeUpstreamWebSocket()
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    registered = anyio.Event()
    original_register = service._register_http_bridge_previous_response_id

    async def register_response(session, response_id, **kwargs):
        result = await original_register(session, response_id, **kwargs)
        assert result
        registered.set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_response)
    headers = {"Authorization": f"Bearer {key['key']}", "thread-id": "astra-late-session"}
    stored_items = [{"role": "user", "content": "first question"}]
    body = {"model": "gpt-6-astra", "instructions": "", "reasoning": {"effort": "low"}}
    with anyio.fail_after(10):
        first = await async_client.post("/v1/responses", json={**body, "input": stored_items}, headers=headers)
        assert first.status_code == 200, first.text
        await registered.wait()

    # Use a real registered session and completed prefix. Delay session selection
    # until response.start, so session-level anchoring exercises the open-stream
    # policy boundary rather than the route's preflight validation.
    headers_started = anyio.Event()
    original_session = service._get_or_create_http_bridge_session

    async def session_after_headers(*args, **kwargs):
        await headers_started.wait()
        return await original_session(*args, **kwargs)

    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", session_after_headers)
    monkeypatch.setattr(proxy_api, "_HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS", 0)

    async def observe_response_start(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "http.response.start":
                headers_started.set()
            await send(message)

        await app_instance(scope, receive, observed_send)

    with anyio.fail_after(10):
        async with AsyncClient(transport=ASGITransport(app=observe_response_start), base_url="http://test") as client:
            response = await client.post(
                path,
                json={
                    **body,
                    "input": [*stored_items, {"role": "user", "content": "second question"}],
                    "stream": True,
                    **extra,
                },
                headers=headers,
            )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    failures = [event for event in events if event.get("type") == "response.failed"]
    if enforced:
        assert len(failures) == 1, response.text
        error = failures[0]["response"]["error"]
        assert error["code"] == "invalid_request_error"
        assert error["type"] == "invalid_request_error"
        assert error["param"] == error_param
        assert len(upstream.sent_text) == 1
    else:
        assert failures == []
        assert sum(event.get("type") == "response.completed" for event in events) == 1
        assert len(upstream.sent_text) == 2
        sent = json.loads(upstream.sent_text[1])
        assert sent["previous_response_id"] == first.json()["id"]
        assert sent["input"] == [{"role": "user", "content": "second question"}]
    connect.assert_awaited_once()
    await service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as db:
        statuses = list((await db.execute(select(ApiKeyUsageReservation.status))).scalars())
    assert sorted(statuses) == (["finalized", "released"] if enforced else ["finalized", "finalized"])
