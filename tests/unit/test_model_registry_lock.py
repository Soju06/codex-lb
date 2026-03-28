from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.core.openai.model_registry import ModelRegistry, ReasoningLevel, UpstreamModel

pytestmark = pytest.mark.unit


def _model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="balanced"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset(),
        raw={},
    )


@pytest.mark.asyncio
async def test_concurrent_updates_are_serialized():
    """Test that concurrent updates to the registry are properly serialized by the lock."""
    registry = ModelRegistry(ttl_seconds=60.0)

    # Create 10 concurrent update tasks with different model data
    async def update_with_model(index: int) -> None:
        model = _model(f"model-{index}")
        await registry.update({"plus": [model]})

    # Run 10 concurrent updates
    tasks = [update_with_model(i) for i in range(10)]
    await asyncio.gather(*tasks)

    # Verify the registry is in a valid state (no corruption)
    snapshot = registry.get_snapshot()
    assert snapshot is not None

    # The snapshot should contain exactly one model (last update wins)
    # This verifies the lock prevents corruption, not that all updates are preserved
    assert len(snapshot.models) == 1

    # The model should be valid and consistent
    model_slug = list(snapshot.models.keys())[0]
    assert model_slug.startswith("model-")
    assert snapshot.models[model_slug].slug == model_slug

    # Verify plan_models is consistent with models
    assert "plus" in snapshot.plan_models
    assert snapshot.plan_models["plus"] == frozenset({model_slug})


@pytest.mark.asyncio
async def test_concurrent_updates_with_overlapping_models():
    """Test concurrent updates with overlapping model data to ensure consistency."""
    registry = ModelRegistry(ttl_seconds=60.0)

    # Create initial state
    await registry.update({"plus": [_model("shared"), _model("initial")]})

    # Run concurrent updates that all modify the same models
    async def update_shared_model(index: int) -> None:
        # Each task updates the shared model with a different version
        model = replace(_model("shared"), priority=index)
        await registry.update({"plus": [model]})

    tasks = [update_shared_model(i) for i in range(5)]
    await asyncio.gather(*tasks)

    # Verify the registry is in a valid state (no corruption)
    snapshot = registry.get_snapshot()
    assert snapshot is not None

    # Should have the shared model (last update wins)
    assert "shared" in snapshot.models

    # The shared model should have one of the priority values
    assert snapshot.models["shared"].priority in range(5)

    # Verify no corruption in data structures
    assert len(snapshot.models) >= 1
    for slug in snapshot.plan_models.get("plus", frozenset()):
        assert slug in snapshot.models
        assert "plus" in snapshot.model_plans[slug]


@pytest.mark.asyncio
async def test_concurrent_updates_preserve_stale_plans():
    """Test that concurrent updates properly preserve stale plan data."""
    registry = ModelRegistry(ttl_seconds=60.0)

    # Initial state with multiple plans
    await registry.update(
        {
            "plus": [_model("plus-only"), _model("shared")],
            "pro": [_model("pro-only"), _model("shared")],
        }
    )

    # Concurrent updates that only update one plan at a time
    async def update_plus_only(index: int) -> None:
        model = _model(f"plus-new-{index}")
        await registry.update({"plus": [model, _model("shared")]})

    async def update_pro_only(index: int) -> None:
        model = _model(f"pro-new-{index}")
        await registry.update({"pro": [model, _model("shared")]})

    # Mix concurrent updates
    tasks = [
        update_plus_only(0),
        update_pro_only(0),
        update_plus_only(1),
        update_pro_only(1),
    ]
    await asyncio.gather(*tasks)

    # Verify the registry is in a valid state
    snapshot = registry.get_snapshot()
    assert snapshot is not None

    # Both plans should be present
    assert "plus" in snapshot.plan_models
    assert "pro" in snapshot.plan_models

    # Shared model should be in both plans
    assert "shared" in snapshot.models
    assert "shared" in snapshot.plan_models["plus"]
    assert "shared" in snapshot.plan_models["pro"]

    # Verify no corruption in the data structures
    for plan_type, model_slugs in snapshot.plan_models.items():
        for slug in model_slugs:
            assert slug in snapshot.models
            assert plan_type in snapshot.model_plans[slug]
