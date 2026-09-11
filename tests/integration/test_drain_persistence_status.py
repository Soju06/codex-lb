"""Internal drain observes detached persistence through native HTTP Responses."""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module
from app.core import shutdown as shutdown_state
from app.db.models import ApiKeyUsageReservation, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService, LimitRuleInput
from tests.integration.test_http_responses_bridge import (
    AccountSelection,
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_drain_status_tracks_real_http_settlement_until_exactly_once_completion(app_instance, monkeypatch):
    _install_bridge_settings(monkeypatch, enabled=True)
    terminal_waiting = asyncio.Event()
    terminal_release = asyncio.Event()
    upstreams = []
    settlement_started = asyncio.Event()
    settlement_release = asyncio.Event()
    settlements = []
    original_finalize = ApiKeysService.finalize_usage_reservation

    async def gated_finalize(self, reservation_id, **kwargs):
        settlements.append(reservation_id)
        settlement_started.set()
        await settlement_release.wait()
        return await original_finalize(self, reservation_id, **kwargs)

    monkeypatch.setattr(ApiKeysService, "finalize_usage_reservation", gated_finalize)

    class ControlledUpstream(_FakeBridgeUpstreamWebSocket):
        async def receive(self):
            message = await super().receive()
            assert message.text is not None
            if json.loads(message.text)["type"] == "response.completed":
                terminal_waiting.set()
                await terminal_release.wait()
            return message

    async with AsyncClient(
        transport=ASGITransport(app=app_instance, client=("127.0.0.1", 50001)), base_url="http://testserver"
    ) as client:
        account_id = await _import_account(client, "drain_http_active", "drain-http@example.com")
        account = await _get_account(account_id)
        async with SessionLocal() as session:
            api_key = await ApiKeysService(ApiKeysRepository(session)).create_key(
                ApiKeyCreateData(
                    name="http drain proof",
                    allowed_models=None,
                    limits=[LimitRuleInput(limit_type="total_tokens", limit_window="weekly", max_value=1000000)],
                )
            )

        async def authenticated_key():
            return api_key

        app_instance.dependency_overrides[proxy_api_module.validate_proxy_api_key] = authenticated_key

        async def select_account(self, deadline, **kwargs):
            return AccountSelection(account=account, error_message=None, error_code=None)

        async def refresh(self, target, **kwargs):
            return target

        async def connect(*args, **kwargs):
            upstream = ControlledUpstream(response_id_prefix=f"resp_drain_http_{len(upstreams)}")
            upstreams.append(upstream)
            return upstream

        monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", select_account)
        monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", refresh)
        monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
        body = {
            "model": "gpt-6-astra",
            "input": "finish active work",
            "stream": True,
            "prompt_cache_key": "drain-http-active-proof",
        }
        shutdown_state.reset()
        request = asyncio.create_task(_collect_sse_events(client, "/backend-api/codex/responses", json_body=body))
        try:
            await asyncio.wait_for(terminal_waiting.wait(), 5)
            assert (await client.post("/internal/drain/start")).status_code == 200
            await asyncio.sleep(0)
            status = (await client.get("/internal/drain/status")).json()["checks"]
            assert int(status["in_flight"]) >= 1
            assert int(status["http_bridge_pending_or_queued_requests"]) >= 1
            assert status["http_bridge_restart_blocking"] == "true"
            assert not request.done()
            assert not upstreams[0].closed
            denied = await client.post("/backend-api/codex/responses", json=body)
            assert denied.status_code == 503
            assert denied.json()["error"]["type"] == "service_unavailable"
            terminal_release.set()
            await asyncio.wait_for(settlement_started.wait(), 5)
            await asyncio.wait_for(asyncio.shield(request), 5)
            settling = (await client.get("/internal/drain/status")).json()["checks"]
            assert int(settling["in_flight"]) == 0, json.dumps(settling)
            assert settling.get("request_persistence_state") == "pending", json.dumps(settling)
            owned_service = get_proxy_service_for_app(app_instance)
            tracked_settlements = [
                task
                for task in owned_service._background_cleanup_tasks
                if task.get_name().startswith("proxy-stream-api-key-settle-") and not task.done()
            ]
            assert len(tracked_settlements) == 1
            assert await owned_service.drain_persistence_tasks(timeout_seconds=0.01) is False
            assert not tracked_settlements[0].cancelled()
            async with SessionLocal() as session:
                before_rows = list((await session.scalars(select(ApiKeyUsageReservation))).all())
                assert len(before_rows) == 1 and before_rows[0].status not in {"finalized", "released"}
            assert settling["request_persistence_pending"] == "1"
            assert settling["http_bridge_pending_or_queued_requests"] == "0"
            assert settling["http_bridge_restart_blocking"] == "false"
            for _ in range(3):
                polled = (await client.get("/internal/drain/status")).json()["checks"]
                assert polled["request_persistence_state"] == "pending"
                assert polled["request_persistence_pending"] == "1"
                assert not tracked_settlements[0].cancelled()
            stopped = await client.post("/internal/drain/stop")
            assert stopped.status_code == 200
            assert not upstreams[0].closed
            assert (await client.get("/internal/drain/status")).json()["checks"]["draining"] == "false"
            terminal_release.set()
            settlement_release.set()
            events = await asyncio.wait_for(request, 5)
            assert await owned_service.drain_persistence_tasks(timeout_seconds=2) is True
            drained = (await client.get("/internal/drain/status")).json()["checks"]
            assert drained["request_persistence_state"] == "drained"
            assert drained["request_persistence_pending"] == "0"
            assert sum(event["type"] == "response.completed" for event in events) == 1
            fresh = await asyncio.wait_for(
                _collect_sse_events(
                    client,
                    "/backend-api/codex/responses",
                    json_body={**body, "prompt_cache_key": "drain-http-fresh-proof"},
                ),
                5,
            )
            assert sum(event["type"] == "response.completed" for event in fresh) == 1
            assert await owned_service.drain_persistence_tasks(timeout_seconds=2) is True
            async with SessionLocal() as session:
                rows = list(
                    (
                        await session.scalars(select(RequestLog).where(RequestLog.request_id == "resp_drain_http_0_1"))
                    ).all()
                )
                assert len(rows) == 1
                assert rows[0].status == "success"
                assert rows[0].input_tokens == 24
                assert rows[0].output_tokens == 2
                assert rows[0].api_key_id == api_key.id
                reservations = list((await session.scalars(select(ApiKeyUsageReservation))).all())
                assert len(reservations) == 2
                assert all(row.status == "finalized" for row in reservations)
                assert len(settlements) == 2
                assert len(set(settlements)) == 2
        finally:
            settlement_release.set()
            app_instance.dependency_overrides.pop(proxy_api_module.validate_proxy_api_key, None)
            terminal_release.set()
            if not request.done():
                request.cancel()
            await asyncio.gather(request, return_exceptions=True)
            service = get_proxy_service_for_app(app_instance)
            for _ in range(3):
                pending = list(service._request_log_tasks | service._background_cleanup_tasks)
                if not pending:
                    break
                await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), 5)
                await asyncio.sleep(0)
            async with service._http_bridge_lock:
                sessions = list(service._http_bridge_sessions.values())
                inflight = list(service._http_bridge_inflight_sessions.values())
                service._http_bridge_sessions.clear()
                service._http_bridge_inflight_sessions.clear()
                service._http_bridge_turn_state_index.clear()
                service._http_bridge_previous_response_index.clear()
            for bridge_session in sessions:
                await service._close_http_bridge_session(bridge_session)
            for future in inflight:
                if not future.done():
                    future.cancel()
            await asyncio.gather(*inflight, return_exceptions=True)
            shutdown_state.reset()
