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
async def test_backend_codex_models_preserves_upstream_visibility(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = resp.json()["models"]
    entry = next(m for m in entries if m["slug"] == "gpt-5.3-codex")
    assert entry["visibility"] == "hide"


@pytest.mark.asyncio
async def test_backend_codex_models_filters_disallowed_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2", base_instructions="allowed"),
        _make_upstream_model("gpt-5.3-codex", base_instructions="blocked"),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "codex-restricted",
            "allowedModels": ["gpt-5.2"],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    entries = resp.json()["models"]
    assert [entry["slug"] for entry in entries] == ["gpt-5.2"]
    assert entries[0]["base_instructions"] == "allowed"


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


@pytest.mark.asyncio
async def test_model_context_window_override(async_client, monkeypatch):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.4")]
    await registry.update({"pro": models})

    from app.core.config import settings as settings_module

    original_settings = settings_module.get_settings()
    patched = original_settings.model_copy(update={"model_context_window_overrides": {"gpt-5.4": 515000}})
    monkeypatch.setattr(settings_module, "get_settings", lambda: patched)

    # /backend-api/codex/models
    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entry = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.4")
    assert entry["context_window"] == 515000

    # /v1/models
    resp_v1 = await async_client.get("/v1/models")
    assert resp_v1.status_code == 200
    v1_entry = next(m for m in resp_v1.json()["data"] if m["id"] == "gpt-5.4")
    assert v1_entry["metadata"]["context_window"] == 515000


@pytest.mark.asyncio
async def test_model_context_window_no_override(async_client):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.4")]
    await registry.update({"pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entry = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.4")
    assert entry["context_window"] == 272000
