from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.fixture
async def cpa_upstream():
    async with stub_source_upstreams() as start:
        yield start


async def test_stalled_sources_share_one_catalog_refresh_budget(async_client):
    release = asyncio.Event()

    async def stalled(request):
        await release.wait()
        return web.json_response({"models": []})

    async with stub_source_upstreams() as start:
        base_url = await start(stalled, handler_cancellation=True, shutdown_timeout=0.1)
        native = (await async_client.get("/v1/models")).json()["data"]
        for index in range(5):
            source = await async_client.post(
                "/api/model-sources/",
                json={
                    "name": f"CPA {index}",
                    "baseUrl": base_url,
                    "catalogMode": "cli_proxy_api",
                    "supportsResponses": True,
                },
            )
            assert source.status_code == 200
        try:
            # Five upstream timeouts must fit one five-second request budget,
            # rather than waiting for a second four-source batch.
            response = await asyncio.wait_for(async_client.get("/v1/models"), timeout=6)
            assert response.status_code == 200
            assert [item["id"] for item in response.json()["data"]] == [item["id"] for item in native]
        finally:
            release.set()


async def test_discovered_collision_keeps_native_catalog_and_routing(async_client):
    native_catalog = (await async_client.get("/v1/models")).json()["data"]
    native = next(item for item in native_catalog if item["id"] == "gpt-5.4")
    inference_calls = []

    async def cpa(request):
        if request.method == "GET":
            return web.json_response({"models": [{"slug": "gpt-5.4", "context_window": 8192}]})
        inference_calls.append(request.path)
        return web.json_response({"id": "wrong_source", "object": "response", "output": []})

    async with stub_source_upstreams() as start:
        source = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "CPA",
                "baseUrl": await start(cpa),
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            },
        )
        assert source.status_code == 200
        refreshed = (await async_client.get("/v1/models")).json()["data"]
        collision = next(item for item in refreshed if item["id"] == "gpt-5.4")
        assert collision["context_length"] == native["context_length"]
        response = await async_client.post("/v1/responses", json={"model": "gpt-5.4", "input": "hello"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "no_accounts"
        assert inference_calls == []


async def test_cached_external_model_returns_explicit_outage(async_client, monkeypatch):
    from datetime import timedelta

    from app.modules.model_sources import discovery

    now = discovery.utcnow()
    monkeypatch.setattr(discovery, "utcnow", lambda: now)
    unavailable = False
    requested_models = []

    async def cpa(request):
        if request.method == "GET" and not unavailable:
            return web.json_response({"models": [{"slug": "cpa-outage", "context_window": 8192}]})
        if request.method == "POST":
            requested_models.append((await request.json())["model"])
        return web.json_response(
            {"error": {"code": "cpa_unavailable", "type": "server_error", "message": "Fixture unavailable"}},
            status=503,
        )

    async with stub_source_upstreams() as start:
        source = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "CPA",
                "baseUrl": await start(cpa),
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            },
        )
        assert source.status_code == 200
        await async_client.get("/v1/models")
        unavailable = True
        now += timedelta(seconds=61)
        catalog = await async_client.get("/v1/models")
        assert "cpa-outage" in [item["id"] for item in catalog.json()["data"]]
        response = await async_client.post("/v1/responses", json={"model": "cpa-outage", "input": "hello"})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "cpa_unavailable"
        assert requested_models == ["cpa-outage"]


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["disable", "manual", "delete", "url"])
async def test_cpa_inflight_refresh_cannot_override_configuration(async_client, cpa_upstream, edit):
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()

    async def catalog(request):
        entered.set()
        await release.wait()
        return web.json_response({"models": [{"slug": "stale-acquisition", "context_window": 8192}]})

    created = await async_client.post(
        "/api/model-sources/",
        json={
            "name": "CPA",
            "baseUrl": await cpa_upstream(catalog),
            "catalogMode": "cli_proxy_api",
            "supportsResponses": True,
        },
    )
    assert created.status_code == 200
    source_id = created.json()["id"]
    pending = asyncio.create_task(async_client.get("/v1/models"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        if edit == "delete":
            changed = await async_client.delete(f"/api/model-sources/{source_id}")
            assert changed.status_code == 204
        else:
            payload = {
                "disable": {"isEnabled": False},
                "manual": {"catalogMode": "manual"},
                "url": {"baseUrl": "http://127.0.0.1:1/v1"},
            }[edit]
            changed = await async_client.patch(f"/api/model-sources/{source_id}", json=payload)
            assert changed.status_code == 200
    finally:
        release.set()
        listed = await pending
    assert all(item["id"] != "stale-acquisition" for item in listed.json()["data"])
    sources = (await async_client.get("/api/model-sources/")).json()["sources"]
    assert not sources or sources[0]["models"] == []


@pytest.mark.asyncio
async def test_concurrent_catalog_reads_share_refresh_and_keep_cadence(async_client, cpa_upstream):
    import asyncio

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def catalog(request):
        calls.append(request.path)
        entered.set()
        await release.wait()
        return web.json_response({"models": [{"slug": "fixture-single-refresh", "context_window": 8192}]})

    created = await async_client.post(
        "/api/model-sources/",
        json={
            "name": "CPA",
            "baseUrl": await cpa_upstream(catalog),
            "catalogMode": "cli_proxy_api",
            "supportsResponses": True,
        },
    )
    assert created.status_code == 200
    pending = asyncio.create_task(async_client.get("/v1/models"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        other = await async_client.get("/v1/models")
        assert other.status_code == 200
    finally:
        release.set()
        await pending
    listed = await async_client.get("/v1/models")
    assert any(item["id"] == "fixture-single-refresh" for item in listed.json()["data"])
    assert calls == ["/v1/models"]
