from __future__ import annotations

from datetime import timedelta

import pytest
from aiohttp import web

from tests.integration.model_source_helpers import stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_manual_to_cpa_disables_old_models_until_successful_snapshot(async_client, monkeypatch):
    from app.modules.model_sources import discovery

    now = discovery.utcnow()
    monkeypatch.setattr(discovery, "utcnow", lambda: now)
    failing = True

    async def catalog(request):
        assert request.method == "GET"
        assert request.path == "/v1/models"
        if failing:
            return web.Response(status=503)
        return web.json_response({"models": [{"slug": "fixture-mode-switch", "context_window": 16384}]})

    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "Switchable source",
                "baseUrl": await start(catalog),
                "supportsResponses": True,
                "models": [{"model": "fixture-mode-switch", "contextWindow": 8192}],
            },
        )
        assert created.status_code == 200
        source = created.json()
        identity = source["models"][0]["id"]
        listed = await async_client.get("/v1/models")
        assert any(item["id"] == "fixture-mode-switch" for item in listed.json()["data"])

        switched = await async_client.patch(f"/api/model-sources/{source['id']}", json={"catalogMode": "cli_proxy_api"})
        assert switched.status_code == 200
        listed = await async_client.get("/v1/models")
        assert listed.status_code == 200
        assert all(item["id"] != "fixture-mode-switch" for item in listed.json()["data"])
        codex = await async_client.get("/backend-api/codex/models")
        assert codex.status_code == 200
        assert all(item["slug"] != "fixture-mode-switch" for item in codex.json()["models"])
        denied = await async_client.post("/v1/responses", json={"model": "fixture-mode-switch", "input": "hello"})
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "model_source_disabled"
        source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        assert source["models"][0]["id"] == identity
        assert source["models"][0]["isEnabled"] is False

        failing = False
        now += timedelta(seconds=61)
        listed = await async_client.get("/v1/models")
        assert any(item["id"] == "fixture-mode-switch" for item in listed.json()["data"])
        source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        assert source["models"][0]["id"] == identity
        assert source["models"][0]["isEnabled"] is True
        assert source["models"][0]["contextWindow"] == 16384


@pytest.mark.asyncio
async def test_cpa_to_manual_disables_old_models_and_returning_snapshot_restores_ids(async_client):
    async def catalog(request):
        assert request.method == "GET"
        return web.json_response({"models": [{"slug": "fixture-discovered", "context_window": 8192}]})

    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "Switchable CPA",
                "baseUrl": await start(catalog),
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            },
        )
        assert created.status_code == 200
        listed = await async_client.get("/v1/models")
        assert any(item["id"] == "fixture-discovered" for item in listed.json()["data"])
        source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        identity = source["models"][0]["id"]

        switched = await async_client.patch(f"/api/model-sources/{source['id']}", json={"catalogMode": "manual"})
        assert switched.status_code == 200
        listed = await async_client.get("/v1/models")
        assert all(item["id"] != "fixture-discovered" for item in listed.json()["data"])
        codex = await async_client.get("/backend-api/codex/models")
        assert all(item["slug"] != "fixture-discovered" for item in codex.json()["models"])
        denied = await async_client.post("/v1/responses", json={"model": "fixture-discovered", "input": "hello"})
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "model_source_disabled"
        source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        assert source["models"][0]["id"] == identity
        assert source["models"][0]["isEnabled"] is False

        switched = await async_client.patch(f"/api/model-sources/{source['id']}", json={"catalogMode": "cli_proxy_api"})
        assert switched.status_code == 200
        listed = await async_client.get("/v1/models")
        assert any(item["id"] == "fixture-discovered" for item in listed.json()["data"])
        source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        assert source["models"][0]["id"] == identity
        assert source["models"][0]["isEnabled"] is True


@pytest.mark.asyncio
async def test_cpa_to_manual_explicit_replacement_remains_enabled(async_client):
    async def catalog(request):
        return web.json_response({"models": [{"slug": "fixture-old", "context_window": 8192}]})

    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "CPA with replacement",
                "baseUrl": await start(catalog),
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            },
        )
        assert created.status_code == 200
        listed = await async_client.get("/v1/models")
        assert any(item["id"] == "fixture-old" for item in listed.json()["data"])
        replaced = await async_client.patch(
            f"/api/model-sources/{created.json()['id']}",
            json={
                "catalogMode": "manual",
                "models": [{"model": "fixture-replacement", "contextWindow": 16384}],
            },
        )
        assert replaced.status_code == 200
        assert [model["model"] for model in replaced.json()["models"]] == ["fixture-replacement"]
        assert replaced.json()["models"][0]["isEnabled"] is True
        listed = await async_client.get("/v1/models")
        assert any(item["id"] == "fixture-replacement" for item in listed.json()["data"])
        assert all(item["id"] != "fixture-old" for item in listed.json()["data"])


@pytest.mark.asyncio
async def test_mode_switch_disables_models_discovered_after_operator_read(async_client, monkeypatch):
    import asyncio

    from app.modules.model_sources.repository import ModelSourcesRepository

    operator_read = asyncio.Event()
    release_operator = asyncio.Event()
    original_get = ModelSourcesRepository.get_by_id
    pause_next_read = True

    async def pause_after_read(repository, source_id):
        nonlocal pause_next_read
        row = await original_get(repository, source_id)
        if pause_next_read:
            pause_next_read = False
            operator_read.set()
            await release_operator.wait()
        return row

    async def catalog(request):
        return web.json_response({"models": [{"slug": "concurrent-discovery", "context_window": 8192}]})

    async with stub_source_upstreams() as start:
        created = await async_client.post(
            "/api/model-sources/",
            json={
                "name": "Concurrent CPA",
                "baseUrl": await start(catalog),
                "catalogMode": "cli_proxy_api",
                "supportsResponses": True,
            },
        )
        assert created.status_code == 200
        monkeypatch.setattr(ModelSourcesRepository, "get_by_id", pause_after_read)
        pending = asyncio.create_task(
            async_client.patch(
                f"/api/model-sources/{created.json()['id']}",
                json={"catalogMode": "manual"},
            )
        )
        try:
            await asyncio.wait_for(operator_read.wait(), timeout=2)
            acquired = await async_client.get("/v1/models")
            assert "concurrent-discovery" in [item["id"] for item in acquired.json()["data"]]
        finally:
            release_operator.set()
            switched = await pending
        assert switched.status_code == 200
        listed = await async_client.get("/v1/models")
        assert "concurrent-discovery" not in [item["id"] for item in listed.json()["data"]]
        source = (await async_client.get("/api/model-sources/")).json()["sources"][0]
        assert source["models"][0]["isEnabled"] is False
        denied = await async_client.post("/v1/responses", json={"model": "concurrent-discovery", "input": "hello"})
        assert denied.status_code == 503
        assert denied.json()["error"]["code"] == "model_source_disabled"
