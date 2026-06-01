from __future__ import annotations

import time
from dataclasses import replace

import pytest

from app.core.openai.model_registry import ModelRegistry, ReasoningLevel, UpstreamModel

pytestmark = pytest.mark.unit

EXPECTED_CORE_MODEL_PLANS = {
    "plus",
    "pro",
    "prolite",
    "team",
    "business",
    "enterprise",
    "edu",
    "education",
    "go",
    "hc",
    "finserv",
    "quorum",
    "self_serve_business_usage_based",
    "enterprise_cbp_usage_based",
}

EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS = {
    "gpt-5.5": "0.124.0",
    "gpt-5.4": "0.98.0",
    "gpt-5.4-mini": "0.98.0",
    "gpt-5.3-codex": "0.98.0",
    "gpt-5.3-codex-spark": "0.100.0",
    "gpt-5.2": "0.0.1",
    "codex-auto-review": "0.98.0",
}


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
async def test_initial_snapshot_is_none():
    registry = ModelRegistry(ttl_seconds=60.0)
    assert registry.get_snapshot() is None


@pytest.mark.asyncio
async def test_plan_types_for_model_returns_none_when_uninitialized():
    registry = ModelRegistry(ttl_seconds=60.0)
    result = registry.plan_types_for_model("some-model")
    assert result is None


def test_plan_types_for_model_uses_bootstrap_when_uninitialized():
    registry = ModelRegistry(ttl_seconds=60.0)

    assert registry.plan_types_for_model("gpt-5.4") == EXPECTED_CORE_MODEL_PLANS
    assert registry.plan_types_for_model("GPT-5.4") == EXPECTED_CORE_MODEL_PLANS


@pytest.mark.asyncio
async def test_plan_types_for_model_returns_empty_for_unknown_model():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"plus": [_model("model-a")]})
    result = registry.plan_types_for_model("unknown-model")
    assert result == frozenset()


@pytest.mark.asyncio
async def test_plan_types_for_model_returns_plans():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update(
        {
            "plus": [_model("model-a"), _model("model-b")],
            "pro": [_model("model-a"), _model("model-c")],
        }
    )

    assert registry.plan_types_for_model("model-a") == frozenset({"plus", "pro"})
    assert registry.plan_types_for_model("model-b") == frozenset({"plus"})
    assert registry.plan_types_for_model("model-c") == frozenset({"pro"})


@pytest.mark.asyncio
async def test_prefers_websockets_uses_snapshot_value():
    registry = ModelRegistry(ttl_seconds=60.0)
    preferred = replace(_model("model-ws"), prefer_websockets=True)
    await registry.update({"plus": [preferred]})

    assert registry.prefers_websockets("model-ws") is True
    assert registry.prefers_websockets("unknown-model") is False


@pytest.mark.asyncio
async def test_prefers_websockets_does_not_use_bootstrap_after_snapshot():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"plus": [_model("model-http")]})

    assert registry.prefers_websockets("gpt-5.3-codex-spark") is False


def test_prefers_websockets_uses_bootstrap_fallback_when_uninitialized():
    registry = ModelRegistry(ttl_seconds=60.0)

    assert registry.prefers_websockets("gpt-5.4") is True
    assert registry.prefers_websockets("gpt-5.4-2026") is True
    assert registry.prefers_websockets("gpt-5.3-codex") is True
    assert registry.prefers_websockets("gpt-5.3-codex-spark") is True
    assert registry.prefers_websockets("gpt-5.4-mini") is True
    assert registry.prefers_websockets("gpt-5.2") is True
    assert registry.prefers_websockets("gpt-5.1") is False


def test_bootstrap_models_include_representative_upstream_metadata():
    registry = ModelRegistry(ttl_seconds=60.0)
    models = registry.get_models_with_fallback()

    assert set(models) == set(EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS)
    for slug, expected_version in EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS.items():
        assert models[slug].minimal_client_version == expected_version

    gpt54 = models["gpt-5.4"]
    assert gpt54.minimal_client_version == "0.98.0"
    assert gpt54.raw["max_context_window"] == 1_000_000
    assert gpt54.available_in_plans == EXPECTED_CORE_MODEL_PLANS

    mini = models["gpt-5.4-mini"]
    assert mini.prefer_websockets is True
    assert mini.default_verbosity == "medium"
    assert mini.minimal_client_version == "0.98.0"
    assert {level.effort for level in mini.supported_reasoning_levels} == {"low", "medium", "high", "xhigh"}

    spark = models["gpt-5.3-codex-spark"]
    assert spark.context_window == 128_000
    assert spark.input_modalities == ("text",)
    assert spark.default_reasoning_level == "high"
    assert spark.supported_in_api is False
    assert spark.minimal_client_version == "0.100.0"

    auto_review = models["codex-auto-review"]
    assert auto_review.raw["visibility"] == "hide"
    assert auto_review.raw["shell_type"] == "shell_command"
    assert auto_review.raw["max_context_window"] == 1_000_000
    assert auto_review.minimal_client_version == "0.98.0"
    assert auto_review.available_in_plans == EXPECTED_CORE_MODEL_PLANS
    assert models["gpt-5.3-codex"].available_in_plans == EXPECTED_CORE_MODEL_PLANS


@pytest.mark.asyncio
async def test_update_merges_models_across_plans():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update(
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


@pytest.mark.asyncio
async def test_partial_update_preserves_stale_plans():
    registry = ModelRegistry(ttl_seconds=60.0)

    # First full update with both plans
    await registry.update(
        {
            "plus": [_model("shared"), _model("plus-only")],
            "pro": [_model("shared"), _model("pro-only")],
        }
    )

    # Partial update: only plus succeeds, pro fails (not in per_plan_results)
    await registry.update(
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


@pytest.mark.asyncio
async def test_needs_refresh_false_after_update():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"plus": [_model("a")]})
    assert registry.needs_refresh() is False


@pytest.mark.asyncio
async def test_needs_refresh_true_after_ttl(monkeypatch):
    registry = ModelRegistry(ttl_seconds=1.0)
    await registry.update({"plus": [_model("a")]})
    assert registry.needs_refresh() is False

    # Simulate time passage by adjusting fetched_at
    snapshot = registry.get_snapshot()
    assert snapshot is not None
    snapshot.fetched_at = time.monotonic() - 2.0
    assert registry.needs_refresh() is True


@pytest.mark.asyncio
async def test_empty_update_is_noop():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({})
    assert registry.get_snapshot() is None


def test_ttl_must_be_positive():
    with pytest.raises(ValueError, match="positive"):
        ModelRegistry(ttl_seconds=0)
    with pytest.raises(ValueError, match="positive"):
        ModelRegistry(ttl_seconds=-1.0)


@pytest.mark.asyncio
async def test_plan_models_reverse_index():
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update(
        {
            "plus": [_model("a"), _model("b")],
            "pro": [_model("b"), _model("c")],
        }
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.plan_models["plus"] == frozenset({"a", "b"})
    assert snapshot.plan_models["pro"] == frozenset({"b", "c"})
