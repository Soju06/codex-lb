from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from aiohttp import web
from sqlalchemy.exc import OperationalError

from app.modules.model_sources import discovery
from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("failed_session", [1, 2, 3])
async def test_refresh_database_failure_serves_stored_catalog(async_client, monkeypatch, failed_session):
    now = discovery.utcnow()
    monkeypatch.setattr(discovery, "utcnow", lambda: now)

    async def catalog(request):
        return web.json_response({"models": [{"slug": "cached-refresh", "context_window": 8192}]})

    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "CPA",
                "baseUrl": await start(catalog),
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            },
        )
        assert created.status_code == 200
        initial = await async_client.get("/v1/models")
        assert "cached-refresh" in [item["id"] for item in initial.json()["data"]]
        now += timedelta(seconds=61)
        session_factory = discovery.get_background_session
        calls = 0

        @asynccontextmanager
        async def interrupted_session():
            nonlocal calls
            calls += 1
            if calls == failed_session:
                raise OperationalError("fixture refresh database failure", {}, RuntimeError("fixture failure"))
            async with session_factory() as session:
                yield session

        # Fault injection is confined to refresh sessions. The public catalog
        # read has its own connection and must still serve the accepted rows.
        monkeypatch.setattr(discovery, "get_background_session", interrupted_session)
        response = await async_client.get("/v1/models")
        assert response.status_code == 200
        assert "cached-refresh" in [item["id"] for item in response.json()["data"]]
        assert calls >= failed_session


@pytest.mark.parametrize("creation_order", [(0, 1, 2, 3, 4), (4, 3, 2, 1, 0)])
async def test_stalled_source_does_not_block_later_sources(async_client, creation_order):
    import asyncio

    first_four_acquired = asyncio.Event()
    release_initial = [asyncio.Event() for _ in range(4)]
    fifth_acquired = asyncio.Event()
    acquisitions = []

    async def catalog(request):
        index = int(request.headers["Authorization"].split()[-1])
        position = len(acquisitions)
        acquisitions.append(index)
        if position < 4:
            if position == 3:
                first_four_acquired.set()
            await release_initial[position].wait()
        else:
            fifth_acquired.set()
        return web.json_response({"models": [{"slug": f"source-{index}", "context_window": 8192}]})

    async with stub_source_upstreams() as start:
        base_url = await start(catalog, handler_cancellation=True, shutdown_timeout=0.1)
        for index in creation_order:
            created = await async_client.post(
                "/api/model-sources/",
                json={
                    "name": f"CPA {index}",
                    "baseUrl": base_url,
                    "apiKey": str(index),
                    "catalogMode": "cli_proxy_api",
                    "supportsResponses": True,
                },
            )
            assert created.status_code == 200
        pending = asyncio.create_task(async_client.get("/v1/models"))
        try:
            await asyncio.wait_for(first_four_acquired.wait(), timeout=2)
            assert len(acquisitions) == 4
            release_initial[0].set()
            # The fifth arrival must reuse this slot while three peers remain blocked.
            await asyncio.wait_for(fifth_acquired.wait(), timeout=2)
            assert len(acquisitions) == 5
        finally:
            for release in release_initial:
                release.set()
            response = await pending
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()["data"]}
        assert {f"source-{index}" for index in range(5)} <= ids


async def test_caller_cancellation_drains_bounded_refresh_workers(async_client):
    import asyncio

    full = asyncio.Event()
    drained = asyncio.Event()
    release = asyncio.Event()
    active = 0
    maximum = 0

    async def catalog(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 4:
            full.set()
        try:
            await release.wait()
            return web.json_response({"models": []})
        finally:
            active -= 1
            if active == 0:
                drained.set()

    async with stub_source_upstreams() as start:
        base_url = await start(catalog, handler_cancellation=True, shutdown_timeout=0.1)
        for index in range(6):
            created = await async_client.post(
                "/api/model-sources/",
                json={
                    "name": f"CPA {index}",
                    "baseUrl": base_url,
                    "catalogMode": "cli_proxy_api",
                    "supportsResponses": True,
                },
            )
            assert created.status_code == 200
        pending = asyncio.create_task(async_client.get("/v1/models"))
        try:
            await asyncio.wait_for(full.wait(), timeout=2)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            await asyncio.wait_for(drained.wait(), timeout=2)
            assert maximum == 4
            assert active == 0
        finally:
            release.set()
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)


async def test_refresh_claim_failure_does_not_cancel_another_source(async_client, monkeypatch):
    async def catalog(request):
        return web.json_response({"models": [{"slug": "healthy-peer", "context_window": 8192}]})

    async with stub_source_upstreams() as start:
        base_url = await start(catalog)
        for index in range(2):
            created = await async_client.post(
                "/api/model-sources/",
                json={
                    "name": f"CPA {index}",
                    "baseUrl": base_url,
                    "catalogMode": "cli_proxy_api",
                    "supportsResponses": True,
                },
            )
            assert created.status_code == 200
        session_factory = discovery.get_background_session
        calls = 0

        @asynccontextmanager
        async def interrupted_session():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OperationalError("fixture claim failure", {}, RuntimeError("fixture failure"))
            async with session_factory() as session:
                yield session

        monkeypatch.setattr(discovery, "get_background_session", interrupted_session)
        response = await async_client.get("/v1/models")
        assert response.status_code == 200
        assert "healthy-peer" in [item["id"] for item in response.json()["data"]]
