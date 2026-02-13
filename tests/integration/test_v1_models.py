from __future__ import annotations

import pytest

from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry

pytestmark = pytest.mark.integration


def _make_upstream_model(slug: str) -> UpstreamModel:
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
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


def _populate_test_registry() -> None:
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
    ]
    registry.update({"plus": models, "pro": models})


@pytest.mark.asyncio
async def test_v1_models_list(async_client):
    _populate_test_registry()
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
