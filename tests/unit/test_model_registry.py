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

# The 21-plan list upstream advertises for GPT-5.6
# (codex-rs/models-manager/models.json at rust-v0.144.1).
EXPECTED_GPT56_MODEL_PLANS = {
    "business",
    "edu",
    "edu_plus",
    "edu_pro",
    "education",
    "enterprise",
    "enterprise_cbp_automation",
    "enterprise_cbp_usage_based",
    "finserv",
    "free",
    "free_workspace",
    "go",
    "hc",
    "k12",
    "plus",
    "pro",
    "prolite",
    "quorum",
    "sci",
    "self_serve_business_usage_based",
    "team",
}

EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS = {
    "gpt-5.6-sol": "0.144.0",
    "gpt-5.6-terra": "0.144.0",
    "gpt-5.6-luna": "0.144.0",
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


def _model_with_support(slug: str, *, supported_in_api: bool) -> UpstreamModel:
    return replace(_model(slug), supported_in_api=supported_in_api)


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
async def test_metadata_retains_full_live_model_after_later_catalog_omits_it():
    registry = ModelRegistry(ttl_seconds=60.0)
    sol = replace(
        _model("gpt-5.6-sol"),
        base_instructions="full live instructions",
        raw={"use_responses_lite": True},
    )
    terra_v1 = replace(_model("gpt-5.6-terra"), description="old terra")
    terra_v2 = replace(_model("gpt-5.6-terra"), description="new terra")

    await registry.update({"plus": [sol, terra_v1]})
    await registry.update({"plus": [terra_v2]})

    assert set(registry.get_models_with_fallback()) == {"gpt-5.6-terra"}
    assert registry.plan_types_for_model("gpt-5.6-sol") == frozenset()
    metadata = registry.get_models_for_metadata()
    assert metadata["gpt-5.6-sol"].base_instructions == "full live instructions"
    assert metadata["gpt-5.6-sol"].raw["use_responses_lite"] is True
    assert metadata["gpt-5.6-terra"].description == "new terra"


@pytest.mark.asyncio
async def test_first_partial_refresh_keeps_bootstrap_metadata_hidden_from_availability():
    registry = ModelRegistry(ttl_seconds=60.0)

    await registry.update({"plus": [_model("gpt-5.6-terra")]})

    assert set(registry.get_models_with_fallback()) == {"gpt-5.6-terra"}
    assert "gpt-5.6-sol" in registry.get_models_for_metadata()
    assert registry.plan_types_for_model("gpt-5.6-sol") == frozenset()


@pytest.mark.asyncio
async def test_metadata_does_not_retain_non_bundled_models():
    registry = ModelRegistry(ttl_seconds=60.0)
    workspace_model = replace(
        _model("workspace-private"),
        raw={"use_responses_lite": False},
    )

    await registry.update({"enterprise": [workspace_model]})
    assert registry.get_models_for_metadata()["workspace-private"].raw["use_responses_lite"] is False

    await registry.update({"enterprise": [_model("gpt-5.6-terra")]})

    assert "workspace-private" not in registry.get_models_for_metadata()


@pytest.mark.parametrize("model_slug", ["gpt-5.5", "gpt-5.3-codex-spark"])
@pytest.mark.asyncio
async def test_plan_types_for_bootstrap_model_uses_live_snapshot_after_refresh(model_slug: str):
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"team": [_model(model_slug)]})

    plans = registry.plan_types_for_model(model_slug)

    assert plans == frozenset({"team"})


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
    await registry.update(
        {"plus": [_model("model-http")]},
        per_account_results={"account-plus": ("plus", [_model("model-http")])},
        active_account_plans={"account-plus": "plus"},
    )

    assert registry.prefers_websockets("gpt-5.3-codex-spark") is False


def test_prefers_websockets_uses_bootstrap_fallback_when_uninitialized():
    registry = ModelRegistry(ttl_seconds=60.0)

    assert registry.prefers_websockets("gpt-5.6-sol") is True
    assert registry.prefers_websockets("gpt-5.6-terra") is True
    assert registry.prefers_websockets("gpt-5.6-luna") is True
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

    sol = models["gpt-5.6-sol"]
    assert sol.display_name == "GPT-5.6-Sol"
    assert sol.context_window == 372_000
    assert sol.default_reasoning_level == "low"
    assert [level.effort for level in sol.supported_reasoning_levels] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]
    assert sol.raw["additional_speed_tiers"] == ["fast"]

    terra = models["gpt-5.6-terra"]
    assert terra.display_name == "GPT-5.6-Terra"
    assert terra.default_reasoning_level == "medium"
    assert [level.effort for level in terra.supported_reasoning_levels] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    ]

    luna = models["gpt-5.6-luna"]
    assert luna.display_name == "GPT-5.6-Luna"
    assert luna.default_reasoning_level == "medium"
    assert [level.effort for level in luna.supported_reasoning_levels] == ["low", "medium", "high", "xhigh", "max"]

    # Upstream-exact GPT-5.6 raw metadata (codex-rs/models-manager/models.json
    # at rust-v0.144.1).
    for gpt56 in (sol, terra, luna):
        assert gpt56.minimal_client_version == "0.144.0"
        assert gpt56.raw["tool_mode"] == "code_mode_only"
        assert gpt56.raw["use_responses_lite"] is True
        assert gpt56.raw["apply_patch_tool_type"] == "freeform"
        assert gpt56.raw["web_search_tool_type"] == "text_and_image"
        assert gpt56.raw["supports_image_detail_original"] is True
        assert gpt56.raw["truncation_policy"] == {"mode": "tokens", "limit": 10_000}
        assert gpt56.raw["comp_hash"] == "3000"
        assert gpt56.raw["reasoning_summary_format"] == "experimental"
        assert gpt56.raw["default_reasoning_summary"] == "none"
        assert gpt56.raw["include_skills_usage_instructions"] is False
        assert gpt56.raw["experimental_supported_tools"] == []
        assert gpt56.raw["supports_search_tool"] is True
        assert gpt56.raw["max_context_window"] == 372_000
        assert gpt56.raw["service_tiers"] == [
            {"id": "priority", "name": "Fast", "description": "1.5x speed, increased usage"}
        ]
        assert gpt56.available_in_plans == EXPECTED_GPT56_MODEL_PLANS
    assert sol.raw["multi_agent_version"] == "v2"
    assert terra.raw["multi_agent_version"] == "v2"
    assert luna.raw["multi_agent_version"] == "v1"
    assert isinstance(sol.raw["availability_nux"], dict)
    assert "most capable model yet" in str(sol.raw["availability_nux"]["message"])
    assert terra.raw["availability_nux"] is None
    assert luna.raw["availability_nux"] is None

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
    assert spark.supported_in_api is True
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
async def test_update_unions_service_tiers_across_plans():
    # Issue #1100: an account/plan without Fast entitlement returns empty
    # service-tier metadata for a shared slug. Last-writer-wins would let that
    # empty list erase Fast from the shared catalog; the merge must union it so
    # Fast stays visible while any account supports it.
    fast = replace(
        _model("gpt-5.5"),
        raw={
            "service_tiers": [{"slug": "fast"}, {"slug": "default"}],
            "additional_speed_tiers": ["fast"],
            "default_service_tier": "fast",
        },
    )
    no_fast = replace(
        _model("gpt-5.5"),
        raw={"service_tiers": [], "additional_speed_tiers": []},
    )

    registry = ModelRegistry(ttl_seconds=60.0)
    # "pro" (Fast) first, "plus" (no Fast) last so last-writer-wins would drop Fast.
    await registry.update({"pro": [fast], "plus": [no_fast]})

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    merged = snapshot.models["gpt-5.5"]
    service_tiers = merged.raw["service_tiers"]
    speed_tiers = merged.raw["additional_speed_tiers"]
    assert isinstance(service_tiers, list)
    assert isinstance(speed_tiers, list)
    tier_slugs = {entry["slug"] for entry in service_tiers if isinstance(entry, dict)}
    assert "fast" in tier_slugs
    assert "fast" in speed_tiers
    assert merged.raw["default_service_tier"] == "fast"


@pytest.mark.asyncio
async def test_update_preserves_non_default_service_tier_default():
    fast = replace(
        _model("gpt-5.5"),
        raw={
            "service_tiers": [{"slug": "fast"}, {"slug": "default"}],
            "additional_speed_tiers": ["fast"],
            "default_service_tier": "fast",
        },
    )
    default_only = replace(
        _model("gpt-5.5"),
        raw={
            "service_tiers": [{"slug": "default"}],
            "additional_speed_tiers": [],
            "default_service_tier": "default",
        },
    )

    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"pro": [fast], "plus": [default_only]})

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    merged = snapshot.models["gpt-5.5"]
    assert merged.raw["default_service_tier"] == "fast"


@pytest.mark.asyncio
async def test_plan_types_for_model_service_tier_tracks_tier_plans():
    fast = replace(
        _model("gpt-5.5"),
        raw={
            "service_tiers": [{"id": "priority", "name": "Fast"}],
            "additional_speed_tiers": ["fast"],
            "default_service_tier": "priority",
        },
    )
    no_fast = replace(
        _model("gpt-5.5"),
        raw={
            "service_tiers": [{"slug": "default"}],
            "additional_speed_tiers": [],
            "default_service_tier": "default",
        },
    )

    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"pro": [fast], "plus": [no_fast]})

    model_plans = registry.plan_types_for_model("gpt-5.5")
    assert model_plans is not None
    assert {"pro", "plus"}.issubset(model_plans)
    assert registry.plan_types_for_model_service_tier("gpt-5.5", "priority") == frozenset({"pro"})
    assert registry.plan_types_for_model_service_tier("gpt-5.5", "fast") == frozenset({"pro"})
    assert registry.plan_types_for_model_service_tier("gpt-5.5", "default") == frozenset({"plus"})


@pytest.mark.asyncio
async def test_account_ids_for_model_service_tier_tracks_account_catalogs():
    fast = replace(
        _model("gpt-5.5"),
        raw={"service_tiers": [{"slug": "fast"}], "additional_speed_tiers": ["fast"]},
    )
    no_fast = replace(_model("gpt-5.5"), raw={"service_tiers": [{"slug": "default"}]})

    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update(
        {"pro": [fast]},
        per_account_results={
            "account-fast": ("pro", [fast]),
            "account-default": ("pro", [no_fast]),
        },
    )

    assert registry.account_ids_for_model_service_tier("gpt-5.5", "priority") == frozenset({"account-fast"})
    assert registry.account_ids_for_model_service_tier("gpt-5.5", "fast") == frozenset({"account-fast"})
    assert registry.account_ids_for_model_service_tier("gpt-5.5", "default") == frozenset({"account-default"})


@pytest.mark.asyncio
async def test_account_ids_for_model_tracks_complete_account_catalogs():
    shared = _model("gpt-5.4")
    sol = _model("gpt-5.6-sol")
    registry = ModelRegistry(ttl_seconds=60.0)

    await registry.update(
        {"pro": [shared, sol]},
        per_account_results={
            "account-sol": ("pro", [shared, sol]),
            "account-default": ("pro", [shared]),
        },
        active_account_plans={"account-sol": "pro", "account-default": "pro"},
    )

    assert registry.account_ids_for_model("gpt-5.6-sol") == frozenset({"account-sol"})
    assert registry.account_ids_for_model("gpt-5.4") == frozenset({"account-sol", "account-default"})
    assert registry.account_ids_for_model("unknown") == frozenset()


@pytest.mark.asyncio
async def test_partial_first_refresh_degrades_account_capabilities_to_unknown():
    shared = _model("gpt-5.4")
    registry = ModelRegistry(ttl_seconds=60.0)

    await registry.update(
        {"pro": [shared]},
        per_account_results={"account-known": ("pro", [shared])},
        active_account_plans={"account-known": "pro", "account-fetch-failed": "pro"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is False
    assert snapshot.bootstrap_floor_active is True
    assert registry.account_ids_for_model("gpt-5.4") is None
    assert registry.account_ids_for_model_service_tier("gpt-5.4", "priority") is None
    assert registry.plan_types_for_model_service_tier("gpt-5.4", "priority") == frozenset({"pro"})
    bootstrap_models = registry.get_models_with_fallback()
    assert "gpt-5.6-sol" in bootstrap_models
    assert registry.plan_types_for_model("gpt-5.6-sol") == EXPECTED_GPT56_MODEL_PLANS
    assert registry.prefers_websockets("gpt-5.6-sol") is True


@pytest.mark.asyncio
async def test_per_account_bundled_model_refreshes_metadata_without_availability():
    live_sol = replace(
        _model("gpt-5.6-sol"),
        base_instructions="live per-account sol metadata",
        raw={"use_responses_lite": False, "service_tiers": [{"slug": "priority"}]},
    )
    registry = ModelRegistry(ttl_seconds=60.0)

    await registry.update(
        {"pro": []},
        per_account_results={
            "account-sol": ("pro", [live_sol]),
            "account-without-sol": ("pro", [_model("gpt-5.6-terra")]),
        },
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert "gpt-5.6-sol" not in snapshot.models
    assert "gpt-5.6-sol" not in snapshot.model_accounts
    assert "gpt-5.6-sol" not in snapshot.model_service_tier_accounts
    assert registry.account_ids_for_model("gpt-5.6-sol") == frozenset()
    assert registry.account_ids_for_model_service_tier("gpt-5.6-sol", "priority") == frozenset()
    assert registry.is_suppressed_model("gpt-5.6-sol") is True
    metadata_sol = registry.get_models_for_metadata()["gpt-5.6-sol"]
    assert metadata_sol.base_instructions == "live per-account sol metadata"
    assert metadata_sol.raw["use_responses_lite"] is False


@pytest.mark.asyncio
async def test_account_ids_for_model_service_tier_preserves_missing_active_accounts():
    fast = replace(_model("gpt-5.5"), raw={"service_tiers": [{"slug": "fast"}], "additional_speed_tiers": ["fast"]})
    no_fast = replace(_model("gpt-5.5"), raw={"service_tiers": [{"slug": "default"}]})

    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update(
        {"pro": [fast]},
        per_account_results={
            "account-fast": ("pro", [fast]),
            "account-default": ("pro", [no_fast]),
        },
        active_account_plans={"account-fast": "pro", "account-default": "pro"},
    )

    await registry.update(
        {"pro": [no_fast]},
        per_account_results={"account-default": ("pro", [no_fast])},
        active_account_plans={"account-fast": "pro", "account-default": "pro"},
    )

    assert registry.account_ids_for_model_service_tier("gpt-5.5", "priority") == frozenset({"account-fast"})
    assert registry.account_ids_for_model_service_tier("gpt-5.5", "default") == frozenset({"account-default"})
    assert registry.account_ids_for_model("gpt-5.5") == frozenset({"account-fast", "account-default"})


@pytest.mark.asyncio
async def test_update_does_not_duplicate_shared_service_tiers():
    # Two accounts that both support Fast must not produce duplicate tier entries.
    fast = replace(
        _model("gpt-5.5"),
        raw={"service_tiers": [{"slug": "fast"}], "additional_speed_tiers": ["fast"]},
    )
    priority = replace(
        _model("gpt-5.5"),
        raw={"service_tiers": [{"id": "priority", "name": "Fast"}], "additional_speed_tiers": ["priority"]},
    )
    registry = ModelRegistry(ttl_seconds=60.0)
    await registry.update({"pro": [fast], "plus": [priority]})

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    merged = snapshot.models["gpt-5.5"]
    assert merged.raw["service_tiers"] == [{"id": "priority", "name": "Fast"}]
    assert merged.raw["additional_speed_tiers"] == ["priority"]


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


@pytest.mark.asyncio
async def test_partial_update_does_not_promote_metadata_only_stale_catalog() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    routable = _model("pro-routable")
    metadata_only = replace(
        _model("pro-metadata-only"),
        raw={"service_tiers": [{"slug": "priority"}]},
    )
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [routable], "plus": [shared]},
        per_account_results={
            "account-pro": ("pro", [routable, metadata_only]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-pro": "pro", "account-plus": "plus"},
    )
    first = registry.get_snapshot()
    assert first is not None
    assert "pro-metadata-only" not in first.models
    assert registry.plan_types_for_model("pro-metadata-only") == frozenset()

    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-pro": "pro", "account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is True
    assert "pro-metadata-only" not in snapshot.models
    assert "pro-metadata-only" not in snapshot.model_accounts
    assert "pro-metadata-only" not in snapshot.model_service_tier_accounts
    assert registry.plan_types_for_model("pro-metadata-only") == frozenset()
    assert registry.plan_types_for_model_service_tier("pro-metadata-only", "priority") == frozenset()
    assert registry.account_ids_for_model("pro-metadata-only") == frozenset()
    assert registry.account_ids_for_model_service_tier("pro-metadata-only", "priority") == frozenset()
    assert registry.is_suppressed_model("pro-metadata-only") is True


@pytest.mark.asyncio
async def test_partial_update_drops_capabilities_for_inactive_accounts():
    registry = ModelRegistry(ttl_seconds=60.0)
    sol = _model("gpt-5.6-sol")
    shared = _model("gpt-5.4")
    await registry.update(
        {"pro": [sol], "plus": [shared]},
        per_account_results={
            "account-pro": ("pro", [sol]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-pro": "pro", "account-plus": "plus"},
    )

    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert "gpt-5.6-sol" not in snapshot.models
    assert registry.account_ids_for_model("gpt-5.6-sol") == frozenset()


@pytest.mark.asyncio
async def test_stale_plan_drops_models_only_removed_account_advertised():
    # Regression for the Codex P2 finding: a stale plan (its only refresh failed)
    # must not re-advertise a model that only a now-removed/paused account served.
    # Setup: plan "pro" has two accounts. account-fail keeps advertising gpt-5.6
    # (its refresh transiently fails but it stays active); account-sol was the ONLY
    # advertiser of gpt-5.6-sol and is then removed. Plan "plus" refreshes cleanly,
    # keeping the snapshot authoritative. The stale-plan carryover must retain
    # gpt-5.6 (still served by the active account-fail) but drop gpt-5.6-sol (no
    # remaining active account can serve it).
    registry = ModelRegistry(ttl_seconds=60.0)
    keep = _model("gpt-5.6")
    sol = _model("gpt-5.6-sol")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [keep, sol], "plus": [shared]},
        per_account_results={
            "account-fail": ("pro", [keep]),
            "account-sol": ("pro", [sol]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={
            "account-fail": "pro",
            "account-sol": "pro",
            "account-plus": "plus",
        },
    )

    # Second refresh: "pro" is stale (account-fail's fetch failed, account-sol is
    # gone), while "plus" refreshes successfully. account-fail stays active.
    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-fail": "pro", "account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    # Transient-fetch-fail active account keeps its last-known model.
    assert "gpt-5.6" in snapshot.models
    assert "pro" in snapshot.model_plans.get("gpt-5.6", frozenset())
    assert "gpt-5.6" in snapshot.plan_models.get("pro", frozenset())
    # Only-advertiser removed -> model leaves discovery entirely.
    assert "gpt-5.6-sol" not in snapshot.models
    assert "gpt-5.6-sol" not in snapshot.plan_models.get("pro", frozenset())
    assert registry.plan_types_for_model("gpt-5.6-sol") == frozenset()
    assert registry.account_ids_for_model("gpt-5.6-sol") == frozenset()


@pytest.mark.asyncio
async def test_stale_plan_drops_removed_advertiser_model_when_previous_non_authoritative():
    # Regression for the third Codex P2: the drop-dead-model carryover invariant must
    # hold even when the PREVIOUS snapshot was NON-authoritative. First refresh: pro
    # account-a succeeds advertising a private model, while same-plan
    # account-b fails (no per-account catalog) -> snapshot is non-authoritative. Then
    # account-a is removed while account-b stays active and pro fails again during
    # plus's refresh. account-a's exclusive model must leave discovery: per last-known
    # per-account catalogs, no currently-active account advertises it.
    registry = ModelRegistry(ttl_seconds=60.0)
    private = _model("private-nonauthoritative-removed")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [private], "plus": [shared]},
        # account-b (pro) failed this pass -> not in per_account_results, so coverage
        # is incomplete and the snapshot is non-authoritative.
        per_account_results={
            "account-a": ("pro", [private]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-a": "pro", "account-b": "pro", "account-plus": "plus"},
    )
    first = registry.get_snapshot()
    assert first is not None
    assert first.account_catalogs_authoritative is False
    assert "private-nonauthoritative-removed" in first.models

    # Second refresh: account-a is removed; account-b (pro) stays active but pro is
    # not refreshed (stale); plus refreshes.
    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-b": "pro", "account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert "private-nonauthoritative-removed" not in snapshot.models
    assert "private-nonauthoritative-removed" not in registry.get_models_with_fallback()
    assert "private-nonauthoritative-removed" not in snapshot.plan_models.get("pro", frozenset())
    assert registry.plan_types_for_model("private-nonauthoritative-removed") == frozenset()
    assert registry.is_suppressed_model("private-nonauthoritative-removed") is True


@pytest.mark.asyncio
async def test_unknown_account_keeps_removed_catalog_model_suppressed_until_readvertise():
    registry = ModelRegistry(ttl_seconds=60.0)
    sol = _model("gpt-5.6-sol")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [sol], "plus": [shared]},
        per_account_results={
            "account-a": ("pro", [sol]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-a": "pro", "account-b": "pro", "account-plus": "plus"},
    )

    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-b": "pro", "account-plus": "plus"},
    )

    second = registry.get_snapshot()
    assert second is not None
    assert second.bootstrap_floor_active is True
    assert "gpt-5.6-sol" not in registry.get_models_with_fallback()
    assert "gpt-5.6-sol" in second.suppressed_model_slugs

    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-b": "pro", "account-plus": "plus"},
    )

    third = registry.get_snapshot()
    assert third is not None
    assert third.bootstrap_floor_active is True
    assert "gpt-5.6-sol" not in registry.get_models_with_fallback()
    assert registry.plan_types_for_model("gpt-5.6-sol") == frozenset()
    assert registry.is_suppressed_model("gpt-5.6-sol") is True
    assert "gpt-5.6-sol" in third.suppressed_model_slugs

    await registry.update(
        {"pro": [sol], "plus": [shared]},
        per_account_results={
            "account-b": ("pro", [sol]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-b": "pro", "account-plus": "plus"},
    )

    fourth = registry.get_snapshot()
    assert fourth is not None
    assert fourth.account_catalogs_authoritative is True
    assert fourth.bootstrap_floor_active is False
    assert "gpt-5.6-sol" in registry.get_models_with_fallback()
    assert "gpt-5.6-sol" not in fourth.suppressed_model_slugs
    assert registry.plan_types_for_model("gpt-5.6-sol") == frozenset({"pro"})


@pytest.mark.asyncio
async def test_complete_refresh_suppresses_removed_private_catalog_model() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    private_alpha = _model("private-alpha")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [private_alpha], "plus": [shared]},
        per_account_results={
            "account-private": ("pro", [private_alpha]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-private": "pro", "account-plus": "plus"},
    )

    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is True
    assert "private-alpha" not in snapshot.models
    assert registry.plan_types_for_model("private-alpha") == frozenset()
    assert registry.is_suppressed_model("private-alpha") is True


@pytest.mark.asyncio
async def test_authoritative_catalog_does_not_suppress_never_known_operator_mapping() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    shared = _model("gpt-5.4")

    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-plus": "plus"},
    )

    assert registry.plan_types_for_model("operator-mapped-never-known") == frozenset()
    assert registry.is_suppressed_model("operator-mapped-never-known") is False


@pytest.mark.asyncio
async def test_first_authoritative_catalog_suppresses_omitted_bootstrap_model() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    advertised = _model("gpt-5.6-terra")

    await registry.update(
        {"pro": [advertised]},
        per_account_results={"account-pro": ("pro", [advertised])},
        active_account_plans={"account-pro": "pro"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is True
    assert snapshot.bootstrap_floor_active is False
    assert "gpt-5.6-terra" in snapshot.models
    assert "gpt-5.6-sol" not in snapshot.models
    assert registry.plan_types_for_model("gpt-5.6-sol") == frozenset()
    assert registry.account_ids_for_model("gpt-5.6-sol") == frozenset()
    assert registry.is_suppressed_model("gpt-5.6-sol") is True
    assert registry.is_suppressed_model("operator-mapped-never-known") is False


@pytest.mark.asyncio
async def test_authoritative_empty_catalog_drops_and_suppresses_stale_model() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    stale_private = _model("private-empty-removed")

    await registry.update(
        {"pro": [stale_private]},
        per_account_results={"account-pro": ("pro", [stale_private])},
        active_account_plans={"account-pro": "pro"},
    )
    assert registry.account_ids_for_model("private-empty-removed") == frozenset({"account-pro"})

    await registry.update(
        {"pro": []},
        per_account_results={"account-pro": ("pro", [])},
        active_account_plans={"account-pro": "pro"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is True
    assert snapshot.bootstrap_floor_active is False
    assert snapshot.models == {}
    assert snapshot.account_plans == {"account-pro": "pro"}
    assert "private-empty-removed" not in snapshot.model_accounts
    assert registry.plan_types_for_model("private-empty-removed") == frozenset()
    assert registry.account_ids_for_model("private-empty-removed") == frozenset()
    assert registry.is_suppressed_model("private-empty-removed") is True
    assert registry.is_suppressed_model("gpt-5.6-sol") is True


@pytest.mark.asyncio
async def test_private_catalog_model_reappearance_clears_suppression() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    private_alpha = _model("private-alpha")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [private_alpha], "plus": [shared]},
        per_account_results={
            "account-private": ("pro", [private_alpha]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-private": "pro", "account-plus": "plus"},
    )
    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-plus": "plus"},
    )
    assert registry.is_suppressed_model("private-alpha") is True

    await registry.update(
        {"pro": [private_alpha], "plus": [shared]},
        per_account_results={
            "account-private-new": ("pro", [private_alpha]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-private-new": "pro", "account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert registry.is_suppressed_model("private-alpha") is False
    assert registry.plan_types_for_model("private-alpha") == frozenset({"pro"})
    assert snapshot.model_accounts["private-alpha"] == frozenset({"account-private-new"})


@pytest.mark.asyncio
async def test_plan_change_drops_failed_account_stale_catalog() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    pro_only = _model("pro-only")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [pro_only], "plus": [shared]},
        per_account_results={
            "account-changed": ("pro", [pro_only]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={"account-changed": "pro", "account-plus": "plus"},
    )

    # The account changes from pro to plus but its new catalog fetch fails. Its
    # old pro-only catalog cannot be reinterpreted as a plus catalog.
    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-changed": "plus", "account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is False
    assert "account-changed" not in snapshot.account_plans
    assert "pro-only" not in snapshot.models
    assert registry.account_ids_for_model("pro-only") is None


@pytest.mark.asyncio
async def test_plan_change_drops_only_changed_account_from_stale_plan() -> None:
    registry = ModelRegistry(ttl_seconds=60.0)
    changed_only = _model("changed-pro-only")
    unchanged_only = _model("unchanged-pro-only")
    shared = _model("gpt-5.4")

    await registry.update(
        {"pro": [changed_only, unchanged_only], "plus": [shared]},
        per_account_results={
            "account-changed": ("pro", [changed_only]),
            "account-unchanged": ("pro", [unchanged_only]),
            "account-plus": ("plus", [shared]),
        },
        active_account_plans={
            "account-changed": "pro",
            "account-unchanged": "pro",
            "account-plus": "plus",
        },
    )

    # Pro stays active but stale through account-unchanged. The failed catalog
    # from account-changed must not be retained or relabeled after it moves to
    # plus, while account-unchanged keeps its last-known pro catalog.
    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={
            "account-changed": "plus",
            "account-unchanged": "pro",
            "account-plus": "plus",
        },
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    assert snapshot.account_catalogs_authoritative is False
    assert "account-changed" not in snapshot.account_plans
    assert "changed-pro-only" not in snapshot.models
    assert "changed-pro-only" not in snapshot.model_accounts
    assert snapshot.account_plans["account-unchanged"] == "pro"
    assert snapshot.model_accounts["unchanged-pro-only"] == frozenset({"account-unchanged"})
    assert snapshot.model_plans["unchanged-pro-only"] == frozenset({"pro"})


@pytest.mark.asyncio
async def test_stale_plan_preserves_models_with_unknown_account_provenance():
    # Degrade-safe carve-out: when a stale plan's model has NO per-account provenance
    # (an older/plan-only snapshot that never captured per-account catalogs), it must
    # be preserved on carryover rather than dropped, even though we now know the active
    # account set. Here the first refresh is plan-only (no per_account_results), so
    # model_accounts is empty; a later refresh of another plan (with active_account_plans)
    # must not drop the pro-only model whose provenance is unknown.
    registry = ModelRegistry(ttl_seconds=60.0)
    pro_only = _model("pro-only")
    shared = _model("gpt-5.4")

    # Plan-only refresh: no per-account catalogs captured -> model_accounts empty.
    await registry.update({"pro": [pro_only], "plus": [shared]})
    first = registry.get_snapshot()
    assert first is not None
    assert first.model_accounts == {}

    # Refresh plus with active-account coverage; pro is stale and account-pro is active.
    await registry.update(
        {"plus": [shared]},
        per_account_results={"account-plus": ("plus", [shared])},
        active_account_plans={"account-pro": "pro", "account-plus": "plus"},
    )

    snapshot = registry.get_snapshot()
    assert snapshot is not None
    # Unknown provenance -> preserved (degrade safe), not dropped.
    assert "pro-only" in snapshot.models
    assert "pro" in snapshot.model_plans.get("pro-only", frozenset())
    plan_types = registry.plan_types_for_model("pro-only")
    assert plan_types is not None
    assert "pro" in plan_types


@pytest.mark.asyncio
async def test_clear_falls_back_to_bootstrap_floor():
    # Regression for the follow-on Codex P2: clearing on zero active accounts must
    # NOT publish an authoritative-empty snapshot that suppresses bootstrap. It must
    # reset to the bootstrap floor (as if never refreshed) so that when an account is
    # added before the next refresh tick, canonical models are still discoverable and
    # still plan-gated (not treated as absent). Bootstrap is the floor whenever there
    # is no authoritative account coverage.
    registry = ModelRegistry(ttl_seconds=60.0)
    live_only_model = _model("live-only-after-clear")
    await registry.update({"plus": [_model("gpt-5.4"), live_only_model]})
    assert "live-only-after-clear" in registry.get_models_for_metadata()

    await registry.clear()

    # No live snapshot -> readers fall back to the static bootstrap catalog.
    assert registry.get_snapshot() is None
    bootstrap_models = registry.get_models_with_fallback()
    assert "gpt-5.6-sol" in bootstrap_models
    # A canonical bootstrap model still exposes its plan gating (non-empty), so
    # _mapped_model_has_registry_entry-style checks keep model/plan filtering on.
    sol_plans = registry.plan_types_for_model("gpt-5.6-sol")
    assert sol_plans is not None and len(sol_plans) > 0
    assert "pro" in sol_plans
    # Per-account coverage is unknown (not authoritatively empty), so routing falls
    # back to plan-level gating instead of excluding every account.
    assert registry.account_ids_for_model("gpt-5.6-sol") is None
    assert registry.account_ids_for_model_service_tier("gpt-5.6-sol", "priority") is None
    assert "live-only-after-clear" not in registry.get_models_for_metadata()


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


def test_is_public_model_requires_supported_in_api_true():
    from app.core.openai.model_registry import is_public_model

    public = _model_with_support("model-public", supported_in_api=True)
    hidden = _model_with_support("model-hidden", supported_in_api=False)

    assert is_public_model(public, None)
    assert not is_public_model(hidden, None)
    assert not is_public_model(hidden, {"model-hidden", "model-public"})


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
