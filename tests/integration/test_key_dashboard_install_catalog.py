from __future__ import annotations

import asyncio
import json
import os
import tomllib
from pathlib import Path

import pytest
from aiohttp import web
from sqlalchemy import func, select, update

from app.db.models import ApiKeyLimit, ApiKeyUsageReservation
from app.db.session import SessionLocal
from tests.integration.model_source_helpers import _create_model_source, _enable_api_key_auth, stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [["cd/gpt-6-astra"], ["cd/linxaq"], ["cd/gpt-6-astra", "cd/linxaq"]])
async def test_admin_picker_offers_both_declared_names_while_key_catalog_filters_them(async_client, allowed):
    await _enable_api_key_auth(async_client)
    public, original = "cd/gpt-6-astra", "cd/linxaq"
    source = await async_client.post(
        "/api/model-sources/",
        json={
            "name": "alias-and-original",
            "baseUrl": "https://upstream.invalid/v1",
            "apiKey": "test-source-token",
            "supportsResponses": True,
            "models": [
                {"model": public, "rawMetadataJson": json.dumps({"upstream_model": original})},
                {"model": original},
            ],
        },
    )
    assert source.status_code == 200, source.text
    picker = await async_client.get("/api/models")
    assert picker.status_code == 200
    assert {public, original} <= {entry["id"] for entry in picker.json()["models"]}
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "two-names-key",
            "assignedSourceIds": [source.json()["id"]],
            "allowedModels": allowed,
            "applyToCodexModel": True,
            "limits": [],
        },
    )
    assert created.status_code == 200, created.text
    headers = {"Authorization": f"Bearer {created.json()['key']}"}
    native = await async_client.get("/api/key-dashboard/models", headers=headers)
    assert native.status_code == 200
    assert {entry["slug"] for entry in native.json()["models"] if entry["visibility"] == "list"} == set(allowed)
    generic = await async_client.get("/v1/models", headers=headers)
    assert generic.status_code == 200
    assert {entry["id"] for entry in generic.json()["data"]} == set(allowed)


@pytest.mark.asyncio
@pytest.mark.parametrize("proxy_auth", [False, True])
async def test_exported_installer_downloads_scoped_aliases_and_preserves_agent_metadata(
    async_client, tmp_path: Path, proxy_auth: bool
):
    if proxy_auth:
        await _enable_api_key_auth(async_client)
    public = "cd/gpt-6-astra"
    source = await _create_model_source(
        async_client,
        name="installer-alias",
        model=public,
        base_url="https://upstream.invalid/v1",
        supports_responses=True,
        raw_metadata_json=json.dumps(
            {
                "upstream_model": "ch/linxaq",
                "base_instructions": "Use collaboration tools. Tiếng Việt 😀",
                "multi_agent_version": "v2",
                "model_messages": {"instructions_template": "Keep {{tools}} metadata"},
            }
        ),
    )
    assigned = [source]
    allowed = [public]
    for kind in ("unassigned", "disallowed", "non-streaming", "chat-only", "disabled"):
        slug = f"custom/{kind}"
        source_id = await _create_model_source(
            async_client,
            name=kind,
            model=slug,
            base_url="https://upstream.invalid/v1",
            supports_responses=kind != "chat-only",
            supports_streaming=kind != "non-streaming",
        )
        if kind != "unassigned":
            assigned.append(source_id)
        if kind != "disallowed":
            allowed.append(slug)
        if kind == "disabled":
            response = await async_client.patch(f"/api/model-sources/{source_id}", json={"isEnabled": False})
            assert response.status_code == 200
    response = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "installer-key",
            "assignedSourceIds": assigned,
            "allowedModels": allowed,
            "applyToCodexModel": True,
            "limits": [],
        },
    )
    assert response.status_code == 200, response.text
    key = response.json()["key"]
    requests: list[str] = []

    async def bridge(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == f"Bearer {key}"
        requests.append(request.path)
        response = await async_client.get(request.path, headers={"Authorization": request.headers["Authorization"]})
        return web.Response(status=response.status_code, body=response.content, content_type="application/json")

    async with stub_source_upstreams() as start:
        origin = (await start(bridge)).removesuffix("/v1")
        exported = await async_client.get(
            origin + "/api/key-dashboard/install-script?platform=linux", headers={"Authorization": f"Bearer {key}"}
        )
        assert exported.status_code == 200
        process = await asyncio.create_subprocess_exec(
            "bash",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "CODEX_HOME": str(tmp_path)},
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(exported.content), timeout=15)
        assert process.returncode == 0, stderr.decode()
        assert key.encode() not in stdout + stderr

    assert requests == ["/api/key-dashboard/models"]
    config = tomllib.loads((tmp_path / "config.toml").read_text())
    assert config["model"] == public
    assert config["model_catalog_json"] == str(tmp_path / "codex-lb-models.json")
    assert config["model_providers"]["codex-lb"]["supports_websockets"] is False
    catalog_text = (tmp_path / "codex-lb-models.json").read_text()
    models = json.loads(catalog_text)["models"]
    assert [model["slug"] for model in models] == [public]
    assert models[0]["multi_agent_version"] == "v2"
    assert models[0]["base_instructions"] == "Use collaboration tools. Tiếng Việt 😀"
    assert models[0]["model_messages"]["instructions_template"] == "Keep {{tools}} metadata"
    assert models[0]["supports_parallel_tool_calls"] is True
    for private in (source, "ch/linxaq", "upstream_model", "token-installer-alias", "upstream.invalid", key):
        assert private not in catalog_text
    assert json.loads((tmp_path / "auth.json").read_text()) == {"OPENAI_API_KEY": key}


@pytest.mark.asyncio
async def test_installer_catalog_authentication_and_cache_headers(async_client):
    path = "/api/key-dashboard/models"
    for headers in ({}, {"Authorization": "Bearer invalid"}):
        response = await async_client.get(path, headers=headers)
        assert response.status_code == 401
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "catalog-key",
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1}],
        },
    )
    assert created.status_code == 200
    async with SessionLocal() as session:
        await session.execute(
            update(ApiKeyLimit).where(ApiKeyLimit.api_key_id == created.json()["id"]).values(current_value=1)
        )
        await session.commit()
    response = await async_client.get(path, headers={"Authorization": f"Bearer {created.json()['key']}"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert "Authorization" in response.headers["vary"].split(", ")
    assert isinstance(response.json()["models"], list)
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(ApiKeyUsageReservation)) == 0
        assert await session.scalar(select(ApiKeyLimit.current_value)) == 1
