from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import cast
from unittest.mock import AsyncMock

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.api as proxy_api
import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import UpstreamWebSocket
from app.core.errors import openai_error
from app.db.models import ApiKeyUsageReservation, HttpBridgeSessionState
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(20)]


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    ("extra", "error_param"),
    [
        ({"truncation": "auto"}, "truncation"),
        ({"context_management": [{"type": "compaction", "compact_threshold": 1000}]}, "context_management"),
    ],
    ids=["auto-truncation", "auto-compaction"],
)
async def test_late_astra_recovery_policy_error_is_terminal_and_releases_reservation(
    async_client, app_instance, monkeypatch, path: str, extra, error_param: str
) -> None:
    account_id = await _import_account(async_client, "astra-late", "astra-late@example.com")
    account = await _get_account(account_id)
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
    assert settings.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "astra-late-policy",
            "allowedReasoningEfforts": ["low"],
            "limits": [{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 1000000}],
        },
    )
    assert created.status_code == 200
    key = created.json()
    _install_bridge_settings(monkeypatch, enabled=True)
    service = get_proxy_service_for_app(app_instance)
    session_key = proxy_module._HTTPBridgeSessionKey("session_header", "astra-late-session", key["id"])
    recovery_upstream = _FakeBridgeUpstreamWebSocket()
    recovery_session = proxy_module._HTTPBridgeSession(
        key=session_key,
        headers={"x-codex-session-id": "astra-late-session"},
        affinity=proxy_module._AffinityPolicy(
            key="astra-late-session", kind=proxy_module.StickySessionKind.CODEX_SESSION
        ),
        request_model="gpt-6-astra",
        account=account,
        upstream=cast(UpstreamWebSocket, recovery_upstream),
        upstream_control=proxy_module._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=service._clock.monotonic(),
        idle_ttl_seconds=120,
    )
    stored_items = [{"role": "user", "content": "first question"}]
    lookup = proxy_module.DurableBridgeLookup(
        session_id="astra-late-durable",
        canonical_kind="session_header",
        canonical_key="astra-late-session",
        api_key_scope=key["id"],
        account_id=account_id,
        owner_instance_id="remote-owner",
        owner_epoch=1,
        lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        state=HttpBridgeSessionState.ACTIVE,
        latest_turn_state=None,
        latest_response_id="resp_astra_late_anchor",
        latest_input_item_count=1,
        latest_input_full_fingerprint=proxy_module._fingerprint_input_items(stored_items),
    )
    owner_forward = proxy_module._HTTPBridgeOwnerForward(
        owner_instance="remote-owner", owner_endpoint="http://owner.invalid", key=session_key
    )
    sessions = AsyncMock(side_effect=[owner_forward, recovery_session])
    monkeypatch.setattr(service._durable_bridge, "lookup_request_targets", AsyncMock(return_value=lookup))
    monkeypatch.setattr(service, "_http_bridge_can_forward_to_active_owner", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_http_bridge_has_live_local_session", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_get_or_create_http_bridge_session", sessions)
    monkeypatch.setattr(proxy_api, "_HTTP_BRIDGE_STARTUP_ERROR_PROBE_SECONDS", 0)
    headers_started = anyio.Event()

    async def failed_owner(**kwargs):
        # Fail after headers, before owner output: real recovery must inject its anchor.
        await headers_started.wait()
        raise ProxyResponseError(502, openai_error("bridge_owner_unreachable", "Owner unavailable"))
        yield ""  # pragma: no cover

    async def observe_response_start(scope, receive, send):
        async def observed_send(message):
            if message["type"] == "http.response.start":
                headers_started.set()
            await send(message)

        await app_instance(scope, receive, observed_send)

    monkeypatch.setattr(service, "_forward_http_bridge_request_to_owner", failed_owner)
    with anyio.fail_after(10):
        async with AsyncClient(transport=ASGITransport(app=observe_response_start), base_url="http://test") as client:
            response = await client.post(
                path,
                json={
                    "model": "gpt-6-astra",
                    "instructions": "",
                    "reasoning": {"effort": "low"},
                    "input": [*stored_items, {"role": "user", "content": "second question"}],
                    "stream": True,
                    **extra,
                },
                headers={"Authorization": f"Bearer {key['key']}", "x-codex-session-id": "astra-late-session"},
            )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    failures = [event for event in events if event.get("type") == "response.failed"]
    assert len(failures) == 1, response.text
    error = failures[0]["response"]["error"]
    assert error["code"] == "invalid_request_error"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == error_param
    assert sessions.await_count == 2
    assert recovery_session.last_completed_response_id == "resp_astra_late_anchor"
    assert recovery_upstream.sent_text == []
    await service.drain_persistence_tasks(timeout_seconds=5)
    async with SessionLocal() as db:
        statuses = list((await db.execute(select(ApiKeyUsageReservation.status))).scalars())
    assert statuses == ["released"]
