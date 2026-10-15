from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event

import app.modules.proxy.context_dispatch as dispatch
import app.modules.proxy.service as proxy_module
from app.db.session import engine
from app.dependencies import get_proxy_service_for_app
from tests.integration.test_codex_context_pool import CONTEXT, SID, envelope, setup_pool
from tests.integration.test_codex_history_notes import _context_key
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.integration


@pytest.fixture
def context_statements():
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "codex_context_" in statement.lower():
            statements.append(statement.lower())

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        yield statements
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


@pytest.mark.parametrize("enabled", [False, True])
async def test_http_dispatch_avoids_context_database_work_on_repeated_turns(
    async_client, monkeypatch, context_statements, enabled
):
    account, _, _, _ = await setup_pool(async_client)
    headers = await _context_key(async_client, [account])

    async def stream(*_args, **_kwargs):
        yield 'data: {"type":"response.completed","response":{"id":"resp_cost","status":"completed","output":[]}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    payload = envelope()
    if not enabled:
        payload.pop("reasoning")
    context_statements.clear()
    response = await async_client.post("/backend-api/codex/responses", headers=headers, json=payload)
    assert response.status_code == 200
    assert "response.completed" in response.text
    if enabled:
        inserts = [s for s in context_statements if s.startswith("insert")]
        assert len(inserts) == 2  # One initial owner fence, then the first participant.
    else:
        assert context_statements == []
    context_statements.clear()
    response = await async_client.post("/backend-api/codex/responses", headers=headers, json=payload)
    assert response.status_code == 200
    assert "response.completed" in response.text
    assert context_statements == []


async def test_rotated_participant_uses_one_insert_without_rebinding(async_client, context_statements):
    owner, next_account, _, key = await setup_pool(async_client)
    await dispatch.record_context_dispatch(envelope(), key, owner)
    context_statements.clear()
    await dispatch.record_context_dispatch(envelope(), key, next_account, record_participant=False)
    assert context_statements == []
    await dispatch.record_context_dispatch(envelope(), key, next_account)
    assert len(context_statements) == 1
    assert context_statements[0].startswith("insert into codex_context_participants")


async def test_http_bridge_prewarm_carries_context_identity_before_send(async_client, monkeypatch):
    from app.db.models import CodexContextSession
    from app.db.session import SessionLocal
    from app.modules.proxy.load_balancer import AccountSelection
    from tests.integration.test_http_responses_bridge import (
        _get_account,
        _install_bridge_settings_with_limits,
        _PrewarmingBridgeUpstreamWebSocket,
    )

    owner, _, headers, key = await setup_pool(async_client)
    account = await _get_account(owner)
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, codex_prewarm_enabled=True)
    upstream = _PrewarmingBridgeUpstreamWebSocket()
    original_send = upstream.send_text

    async def send(text):
        async with SessionLocal() as session:
            binding = await session.get(CodexContextSession, SID)
            assert binding is not None and binding.api_key_id == key.id
        await original_send(text)

    async def refresh(_self, account, **_kwargs):
        return account

    monkeypatch.setattr(upstream, "send_text", send)
    monkeypatch.setattr(
        proxy_module.ProxyService,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", refresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", AsyncMock(return_value=upstream))
    response = await async_client.post(
        "/backend-api/codex/responses",
        headers={**headers, "x-codex-turn-state": "context-prewarm"},
        json=envelope(),
    )
    assert response.status_code == 200 and "response.completed" in response.text
    assert len(upstream.sent_text) == 2
    assert json.loads(upstream.sent_text[0])["generate"] is False


@pytest.mark.parametrize("cache_miss", ["restart", "eviction"])
async def test_marked_cache_miss_still_checks_durable_key_ownership(async_client, monkeypatch, cache_miss):
    owner, _, _, key = await setup_pool(async_client)
    cache = dispatch.ContextDispatchCache(max_entries=1)
    monkeypatch.setattr(dispatch, "_dispatch_cache", cache)
    await dispatch.record_context_dispatch(envelope(), key, owner)
    if cache_miss == "restart":
        cache.clear()
    else:
        another = envelope()
        another["client_metadata"]["session_id"] = "00000000-0000-4000-8000-000000000099"
        await dispatch.record_context_dispatch(another, key, owner)
    assert cache.get(SID, key.id) is None
    other_headers = await _context_key(async_client, [])
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    response = await async_client.post("/backend-api/codex/responses", headers=other_headers, json=envelope())
    assert response.status_code == 403
    upstream.assert_not_called()


async def test_failed_context_commit_is_not_cached(async_client):
    owner, _, _, key = await setup_pool(async_client)
    cache = dispatch.get_context_dispatch_cache()

    def fail_commit(_conn):
        raise RuntimeError("injected commit failure")

    event.listen(engine.sync_engine, "commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="injected commit failure"):
            await dispatch.record_context_dispatch(envelope(), key, owner)
    finally:
        event.remove(engine.sync_engine, "commit", fail_commit)
    assert cache.get(SID, key.id) is None
    await dispatch.record_context_dispatch(envelope(), key, owner)
    assert cache.get(SID, key.id) is not None


@pytest.mark.parametrize("finish", ["timeout", "cancel"])
async def test_history_fanout_owns_deadline_and_cleans_up_all_tasks(async_client, app_instance, monkeypatch, finish):
    owner, other, _, key = await setup_pool(async_client)
    await dispatch.record_context_dispatch(envelope(), key, owner)
    await dispatch.record_context_dispatch(envelope(), key, other)
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    service = get_proxy_service_for_app(app_instance)
    started = set()
    finished = set()
    both_started = asyncio.Event()
    upstream_budget = []

    async def upstream(_path, **kwargs):
        account_id = kwargs["account_id"]
        started.add(account_id)
        upstream_budget.append(kwargs["timeout_seconds"])
        if len(started) == 2:
            both_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.add(account_id)

    with monkeypatch.context() as patch:
        patch.setattr(service, "_clock", clock)
        patch.setattr(service, "_scheduler", scheduler)
        patch.setattr(service, "_write_request_log", AsyncMock())
        patch.setattr(proxy_module, "core_codex_control_request", upstream)
        task = scheduler.create_task(
            service.codex_context_request(
                "alpha/history/v2/list_windows",
                payload=json.dumps({"context": CONTEXT}).encode(),
                headers={},
                query_params=[],
                api_key=key,
            )
        )
        try:
            await both_started.wait()
            assert upstream_budget == [30.0, 30.0]
            assert len(scheduler.owned_tasks) == 3  # Request plus two history partitions.
            await scheduler.advance(29)
            assert not task.done()
            if finish == "timeout":
                await scheduler.advance(1)
                with pytest.raises(TimeoutError):
                    await task
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert finished == started
            assert all(owned.done() for owned in scheduler.owned_tasks)
            assert scheduler.pending_timers == 0
        finally:
            await scheduler.cancel_owned_tasks()
