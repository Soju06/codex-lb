from __future__ import annotations

import time

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


def test_initial_snapshot_is_none():
    registry = ModelRegistry(ttl_seconds=60.0)
    assert registry.get_snapshot() is None


def test_plan_types_for_model_returns_none_when_uninitialized():
    registry = ModelRegistry(ttl_seconds=60.0)
    result = registry.plan_types_for_model("some-model")
    assert result is None


def test_plan_types_for_model_returns_empty_for_unknown_model():
    registry = ModelRegistry(ttl_seconds=60.0)
    registry.update({"plus": [_model("model-a")]})
    result = registry.plan_types_for_model("unknown-model")
    assert result == frozenset()


def test_plan_types_for_model_returns_plans():
    registry = ModelRegistry(ttl_seconds=60.0)
    registry.update(
        {
            "plus": [_model("model-a"), _model("model-b")],
            "pro": [_model("model-a"), _model("model-c")],
        }
    )

    assert registry.plan_types_for_model("model-a") == frozenset({"plus", "pro"})
    assert registry.plan_types_for_model("model-b") == frozenset({"plus"})
    assert registry.plan_types_for_model("model-c") == frozenset({"pro"})


def test_update_merges_models_across_plans():
    registry = ModelRegistry(ttl_seconds=60.0)
    registry.update(
        {
            "plus": [_model("shared"), _model("plus-only")],
            "pro": [_model("shared"), _model("pro-only")],
        }
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert set(snapshot.models.keys()) == {"shared", "plus-only", "pro-only"}
    assert snapshot.plan_models["plus"] == frozenset({"shared", "plus-only"})
    assert snapshot.plan_models["pro"] == frozenset({"shared", "pro-only"})


def test_partial_update_preserves_stale_plans():
    registry = ModelRegistry(ttl_seconds=60.0)

    # First full update with both plans
    registry.update(
        {
            "plus": [_model("shared"), _model("plus-only")],
            "pro": [_model("shared"), _model("pro-only")],
        }
    )

    # Partial update: only plus succeeds, pro fails (not in per_plan_results)
    registry.update(
        {
            "plus": [_model("shared"), _model("plus-new")],
        }
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None

    # pro-only should be preserved from previous snapshot
    assert "pro-only" in snapshot.models
    assert "pro" in snapshot.model_plans.get("pro-only", frozenset())

    # plus-only should be gone (not in new plus results)
    assert "plus-only" not in snapshot.models

    # plus-new should be present
    assert "plus-new" in snapshot.models
    assert "plus" in snapshot.model_plans["plus-new"]


def test_needs_refresh_true_initially():
    registry = ModelRegistry(ttl_seconds=60.0)
    assert registry.needs_refresh() is True


def test_needs_refresh_false_after_update():
    registry = ModelRegistry(ttl_seconds=60.0)
    registry.update({"plus": [_model("a")]})
    assert registry.needs_refresh() is False


def test_needs_refresh_true_after_ttl(monkeypatch):
    registry = ModelRegistry(ttl_seconds=1.0)
    registry.update({"plus": [_model("a")]})
    assert registry.needs_refresh() is False

    # Simulate time passage by adjusting fetched_at
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    snapshot.fetched_at = time.monotonic() - 2.0
    assert registry.needs_refresh() is True


def test_empty_update_is_noop():
    registry = ModelRegistry(ttl_seconds=60.0)
    registry.update({})
    assert registry.get_snapshot() is None


def test_ttl_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        ModelRegistry(ttl_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        ModelRegistry(ttl_seconds=-1.0)


def test_plan_models_reverse_index():
    registry = ModelRegistry(ttl_seconds=60.0)
    registry.update(
        {
            "plus": [_model("a"), _model("b")],
            "pro": [_model("b"), _model("c")],
        }
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.plan_models["plus"] == frozenset({"a", "b"})
    assert snapshot.plan_models["pro"] == frozenset({"b", "c"})
