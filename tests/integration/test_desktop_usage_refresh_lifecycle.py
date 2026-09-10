from __future__ import annotations

import asyncio
from datetime import timezone

import pytest
from sqlalchemy import event
from sqlalchemy.pool import Pool

from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import AccountStatus
from tests.integration.test_desktop_usage_api import HEADERS, ORIGINAL, seed_pool

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.usage_refresh_request_path]


@pytest.fixture
def checked_out_connections():
    connections = set()

    def checkout(connection, record, proxy):
        connections.add(id(record))

    def checkin(connection, record):
        connections.discard(id(record))

    event.listen(Pool, "checkout", checkout)
    event.listen(Pool, "checkin", checkin)
    try:
        yield connections
    finally:
        event.remove(Pool, "checkout", checkout)
        event.remove(Pool, "checkin", checkin)


@pytest.mark.parametrize("interruption", ["deadline", "caller_cancel"])
async def test_desktop_shared_refresh_persists_after_waiter_leaves(
    async_client, db_setup, monkeypatch, interruption, checked_out_connections
):
    await seed_pool(stale=True, second_status=AccountStatus.PAUSED)
    entered = asyncio.Event()
    release = asyncio.Event()
    shared_tasks = []
    fetch_calls = 0
    reset_at = int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 300

    async def identity(**kwargs):
        return UsagePayload.from_upstream(ORIGINAL)

    async def fetch(**kwargs):
        nonlocal fetch_calls
        fetch_calls += 1
        shared_tasks.append(asyncio.current_task())
        entered.set()
        await release.wait()
        return UsagePayload.from_upstream(
            {
                "plan_type": "plus",
                "additional_rate_limits": [
                    {
                        "limit_name": "gpt-6-astra",
                        "metered_feature": "astra",
                        "rate_limit": {
                            "allowed": True,
                            "limit_reached": False,
                            "primary_window": {"used_percent": 25, "limit_window_seconds": 18000, "reset_at": reset_at},
                        },
                    }
                ],
                "rate_limit": {
                    "allowed": True,
                    "limit_reached": False,
                    "primary_window": {"used_percent": 25, "limit_window_seconds": 18000, "reset_at": reset_at},
                    "secondary_window": {"used_percent": 25, "limit_window_seconds": 604800, "reset_at": reset_at},
                },
            }
        )

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", identity)
    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", fetch)
    monkeypatch.setattr(
        "app.modules.desktop_usage.service._REFRESH_TIMEOUT_SECONDS", 0.2 if interruption == "deadline" else 5
    )
    request = asyncio.create_task(async_client.get("/api/codex/desktop/usage", headers=HEADERS))
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        if interruption == "caller_cancel":
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
        else:
            response = await asyncio.wait_for(request, timeout=5)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "pooled_usage_unavailable"

        assert len(shared_tasks) == 1
        shared_refresh = shared_tasks[0]
        assert shared_refresh is not None
        assert not shared_refresh.done()
        # No transaction may stay checked out while upstream I/O is blocked.
        assert not checked_out_connections
        release.set()
        await asyncio.wait_for(asyncio.shield(shared_refresh), timeout=5)

        # A new caller reads the completed write, without another upstream fetch.
        response = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["rate_limit"]["primary_window"]["used_percent"] == 25
        assert response.json()["rate_limit"]["secondary_window"]["used_percent"] == 25
        assert fetch_calls == 1
        assert not checked_out_connections
    finally:
        release.set()
        request.cancel()
        await asyncio.gather(request, *shared_tasks, return_exceptions=True)
