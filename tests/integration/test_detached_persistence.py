from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.proxy import service as proxy_service_module

pytestmark = pytest.mark.integration


def _sse_event(event: dict) -> str:
    import json

    return f"data: {json.dumps(event)}\n\n"


@pytest.fixture
async def raw_client(app_instance):
    """async_client without the conftest drain hook: observes production
    semantics, where persistence detaches from the response path."""
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client, app_instance


@pytest.mark.asyncio
async def test_stream_close_does_not_wait_for_request_log_persistence(raw_client, monkeypatch):
    """The detach contract: the response can complete while the log INSERT is
    still pending; draining the service then persists it exactly once."""
    import asyncio

    client, app = raw_client

    from tests.integration.test_proxy_api_extended import _import_account

    account_id = await _import_account(client, "acc_detach", "detach@example.com")

    gate = asyncio.Event()
    from app.modules.request_logs.repository import RequestLogsRepository

    original_add_log = RequestLogsRepository.add_log

    async def gated_add_log(self, *args, **kwargs):
        await gate.wait()
        return await original_add_log(self, *args, **kwargs)

    monkeypatch.setattr(RequestLogsRepository, "add_log", gated_add_log)

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield _sse_event({"type": "response.completed", "response": {"id": "resp_detach", "usage": None}})

    monkeypatch.setattr(proxy_service_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": "req_detach_1"},
    ) as resp:
        assert resp.status_code == 200
        [line async for line in resp.aiter_lines()]

    # Response is fully closed while add_log is still gated: nothing persisted.
    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).where(RequestLog.account_id == account_id))).scalars().all()
    assert rows == []

    gate.set()
    service = app.state.proxy_service
    assert await service.drain_persistence_tasks(timeout_seconds=5)

    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).where(RequestLog.account_id == account_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].request_id == "resp_detach"
    assert rows[0].status == "success"


@pytest.mark.asyncio
async def test_drain_persistence_tasks_reports_timeout():
    import asyncio
    from contextlib import asynccontextmanager
    from typing import cast

    @asynccontextmanager
    async def repo_factory():
        yield object()

    service = proxy_service_module.ProxyService(cast(proxy_service_module.ProxyRepoFactory, repo_factory))

    async def never_finishes() -> None:
        await asyncio.Event().wait()

    task = asyncio.get_running_loop().create_task(never_finishes(), name="stuck-persistence")
    service._request_log_tasks.add(task)
    try:
        assert await service.drain_persistence_tasks(timeout_seconds=0.05) is False
    finally:
        task.cancel()
        service._request_log_tasks.discard(task)
