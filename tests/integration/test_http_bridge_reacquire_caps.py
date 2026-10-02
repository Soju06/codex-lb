from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.load_balancer as load_balancer_module
import app.modules.proxy.service as proxy_module
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import streaming as bridge_streaming
from app.modules.proxy.load_balancer import AccountSelection, effective_account_concurrency_caps
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401 -- retain the suite's session/task teardown
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_proxy_settings,
    _make_app_settings,
    _make_dashboard_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("keyed", [False, True], ids=["unkeyed", "keyed"])
@pytest.mark.parametrize(
    ("dashboard_limit", "occupied_count", "admitted"),
    [(128, 8, True), (0, 8, True), (2, 2, False), (None, 8, False)],
    ids=["dashboard-128", "unlimited", "lower-cap", "inherited-cap"],
)
async def test_http_bridge_reacquire_honors_dashboard_stream_cap(
    async_client, app_instance, monkeypatch, path, keyed, dashboard_limit, occupied_count, admitted
):
    startup = _make_app_settings(enabled=True)
    assert startup.proxy_account_stream_limit == 8
    dashboard = _make_dashboard_settings()
    dashboard.proxy_account_stream_limit = dashboard_limit
    dashboard.proxy_account_response_create_limit = 32
    _install_proxy_settings(monkeypatch, app_settings=startup, dashboard_settings=dashboard)
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: startup)
    # Preserve the real refusal; avoid waiting 30 seconds per retry on broken code.
    monkeypatch.setattr(bridge_streaming, "_http_bridge_capacity_wait_plan", lambda *_args, **_kwargs: None)
    account_id = await _import_account(async_client, "reacquire-caps", "reacquire-caps@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    upstream = _FakeBridgeUpstreamWebSocket()
    headers = {"session_id": "reacquire-caps"}
    key_id = None
    if keyed:
        enabled = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True})
        assert enabled.status_code == 200
        created = await async_client.post("/api/api-keys/", json={"name": "reacquire-caps"})
        assert created.status_code == 200
        key_id = created.json()["id"]
        headers["Authorization"] = f"Bearer {created.json()['key']}"

    async def select_account(*_args, **_kwargs):
        # Initial selection owns a real lease with the dashboard cap. The
        # follow-up runs the unmocked submit/reacquire/balancer path.
        lease = await service._load_balancer.acquire_account_lease(
            account_id,
            kind="stream",
            concurrency_caps=effective_account_concurrency_caps(dashboard),
            api_key_id=key_id,
        )
        assert lease is not None
        return AccountSelection(account=account, error_message=None, lease=lease)

    monkeypatch.setattr(service, "_select_account_with_budget", select_account)
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    body = {"model": "gpt-5.1", "input": "first turn", "stream": True}
    first = await async_client.post(path, json=body, headers=headers)
    assert first.status_code == 200
    events = [json.loads(line[6:]) for line in first.text.splitlines() if line.startswith("data: {")]
    assert events[-1]["type"] == "response.completed"
    session = next(iter(service._http_bridge_sessions.values()))
    assert session.key.api_key_id == key_id
    assert session.account_lease is None
    assert await service._load_balancer.account_pressure_snapshot(account_id) == (0, 0, 0.0)

    occupied = []
    try:
        for _ in range(occupied_count):
            lease = await service._load_balancer.acquire_account_lease(
                account_id, kind="stream", concurrency_caps=effective_account_concurrency_caps(dashboard)
            )
            assert lease is not None
            occupied.append(lease)
        body.update(input="follow-up", previous_response_id=events[-1]["response"]["id"])
        second = await asyncio.wait_for(async_client.post(path, json=body, headers=headers), timeout=5)
        if admitted:
            assert second.status_code == 200, second.text
            events = [json.loads(line[6:]) for line in second.text.splitlines() if line.startswith("data: {")]
            assert events[-1]["type"] == "response.completed", second.text
        else:
            assert second.status_code == 429, second.text
            assert second.json()["error"]["code"] == "account_stream_cap"
        assert len(upstream.sent_text) == 1 + int(admitted)
        connect.assert_awaited_once()
        assert session.account_lease is None
        assert session.admission_waiter_count == 0
        assert session.queued_request_count == 0
        assert await service._load_balancer.account_pressure_snapshot(account_id) == (0, occupied_count, 0.0)
    finally:
        for lease in occupied:
            await service._load_balancer.release_account_lease(lease)
    assert await service._load_balancer.account_pressure_snapshot(account_id) == (0, 0, 0.0)
