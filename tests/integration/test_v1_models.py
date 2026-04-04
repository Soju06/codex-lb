from __future__ import annotations

import pytest

from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry

pytestmark = pytest.mark.integration


def _make_upstream_model(
    slug: str,
    *,
    supported_in_api: bool = True,
    base_instructions: str = "",
    raw: dict | None = None,
) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=272000,
        input_modalities=("text", "image"),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=True,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=supported_in_api,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        base_instructions=base_instructions,
        raw=raw
        or {
            "shell_type": "shell_command",
            "visibility": "list",
            "availability_nux": None,
        },
    )


async def _populate_test_registry() -> None:
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
    ]
    await registry.update({"plus": models, "pro": models})


@pytest.mark.asyncio
async def test_v1_models_list(async_client):
    await _populate_test_registry()
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    data = payload["data"]
    assert isinstance(data, list)
    ids = {item["id"] for item in data}
    assert "gpt-5.2" in ids
    assert "gpt-5.3-codex" in ids
    for item in data:
        assert item["object"] == "model"
        assert item["owned_by"] == "codex-lb"
        assert "metadata" in item


@pytest.mark.asyncio
async def test_v1_models_empty_when_registry_not_populated(async_client):
    registry = get_model_registry()
    registry._snapshot = None
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    assert payload["data"] == []


@pytest.mark.asyncio
async def test_v1_models_includes_supported_in_api_false_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["data"]}
    assert {"gpt-5.2", "gpt-5.3-codex", "gpt-hidden"}.issubset(ids)


@pytest.mark.asyncio
async def test_backend_codex_models_returns_format1(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "availability_nux": None,
                "upgrade": {"model": "gpt-5.4", "migration_markdown": "Upgrade!"},
            },
        ),
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert "models" in payload
    assert isinstance(payload["models"], list)
    slugs = {m["slug"] for m in payload["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(slugs)


@pytest.mark.asyncio
async def test_backend_codex_models_entry_has_upstream_fields(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "availability_nux": None,
                "upgrade": {"model": "gpt-5.4", "migration_markdown": "Upgrade!"},
            },
            base_instructions="You are a helpful coding assistant.",
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = resp.json()["models"]
    entry = next(m for m in entries if m["slug"] == "gpt-5.3-codex")

    assert entry["display_name"] == "gpt-5.3-codex"
    assert entry["description"] == "Test model gpt-5.3-codex"
    assert entry["base_instructions"] == "You are a helpful coding assistant."
    assert entry["context_window"] == 272000
    assert entry["supported_in_api"] is True
    assert entry["shell_type"] == "shell_command"
    assert entry["visibility"] == "list"
    assert entry["availability_nux"] is None
    assert entry["upgrade"] == {"model": "gpt-5.4", "migration_markdown": "Upgrade!"}


@pytest.mark.asyncio
async def test_backend_codex_models_includes_supported_in_api_false_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    slugs = {m["slug"] for m in resp.json()["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex", "gpt-hidden"}.issubset(slugs)


@pytest.mark.asyncio
async def test_backend_codex_models_empty_when_registry_not_populated(async_client):
    registry = get_model_registry()
    registry._snapshot = None
    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["models"] == []


@pytest.mark.asyncio
async def test_model_sets_are_consistent_across_api_endpoints(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    dashboard = await async_client.get("/api/models")
    v1 = await async_client.get("/v1/models")
    codex = await async_client.get("/backend-api/codex/models")

    assert dashboard.status_code == 200
    assert v1.status_code == 200
    assert codex.status_code == 200

    dashboard_ids = {item["id"] for item in dashboard.json()["models"]}
    v1_ids = {item["id"] for item in v1.json()["data"]}
    codex_slugs = {item["slug"] for item in codex.json()["models"]}
    assert dashboard_ids == v1_ids == codex_slugs
