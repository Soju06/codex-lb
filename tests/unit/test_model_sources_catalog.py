from __future__ import annotations

import json

import pytest

from app.core.openai.model_registry import MODEL_SOURCE_KIND_OPENAI_COMPATIBLE
from app.db.models import ModelSource, ModelSourceModel
from app.modules.model_sources.catalog import (
    DEFAULT_SOURCE_CONTEXT_WINDOW,
    source_model_audio_cost_usd,
    source_model_reasoning_levels,
    source_model_request_overrides,
    source_model_supported_tool_types,
    source_model_supports_reasoning,
    source_models_to_upstream_models,
)


def _audio_source(audio_per_minute: float | None) -> ModelSource:
    return ModelSource(
        id="src_asr",
        name="ASR",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8000/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=False,
        supports_audio_transcriptions=True,
        models=[
            ModelSourceModel(
                model="whisper-large-v3",
                is_enabled=True,
                audio_per_minute=audio_per_minute,
            )
        ],
    )


def test_source_model_audio_cost_usd_bills_by_minute() -> None:
    source = _audio_source(0.006)
    # 90 seconds == 1.5 minutes @ $0.006/min == $0.009
    assert source_model_audio_cost_usd(source, "whisper-large-v3", 90.0) == pytest.approx(0.009)


def test_source_model_audio_cost_usd_none_without_rate() -> None:
    source = _audio_source(None)
    assert source_model_audio_cost_usd(source, "whisper-large-v3", 90.0) is None


def test_source_model_audio_cost_usd_zero_for_nonpositive_duration() -> None:
    source = _audio_source(0.006)
    assert source_model_audio_cost_usd(source, "whisper-large-v3", 0.0) == 0.0


def test_source_models_to_upstream_models_preserves_source_identity() -> None:
    source = ModelSource(
        id="src_local",
        name="Local vLLM",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8000/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=False,
        models=[
            ModelSourceModel(
                model="local-coder",
                display_name="Local Coder",
                context_window=32768,
                max_output_tokens=4096,
                supports_streaming=True,
                supports_tools=True,
                supports_vision=False,
                is_enabled=True,
            )
        ],
    )

    models = source_models_to_upstream_models([source])

    assert len(models) == 1
    model = models[0]
    assert model.slug == "local-coder"
    assert model.source_kind == MODEL_SOURCE_KIND_OPENAI_COMPATIBLE
    assert model.source_id == "src_local"
    assert model.context_window == 32768
    assert model.raw["max_output_tokens"] == 4096
    assert model.supports_parallel_tool_calls is True
    assert model.prefer_websockets is False


def test_source_models_to_upstream_models_defaults_missing_context_window() -> None:
    source = ModelSource(
        id="src_ollama",
        name="Ollama",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        models=[
            ModelSourceModel(
                model="llama3.1:8b",
                is_enabled=True,
            )
        ],
    )

    models = source_models_to_upstream_models([source])

    assert len(models) == 1
    model = models[0]
    assert model.context_window == DEFAULT_SOURCE_CONTEXT_WINDOW
    assert model.raw["shell_type"] == "shell_command"
    assert model.raw["max_context_window"] == DEFAULT_SOURCE_CONTEXT_WINDOW
    assert model.raw["truncation_policy"] == {"mode": "tokens", "limit": 10_000}
    assert model.raw["include_skills_usage_instructions"] is False
    assert model.raw["supports_image_detail_original"] is False
    assert model.raw["supports_search_tool"] is False
    assert model.raw["use_responses_lite"] is False
    assert model.raw["experimental_supported_tools"] == []
    assert model.prefer_websockets is False


def _overrides_source(raw_metadata: dict[str, object]) -> ModelSource:
    return ModelSource(
        id="src_overrides",
        name="Overrides",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:11434/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        models=[
            ModelSourceModel(
                model="llama3.1:8b",
                raw_metadata_json=json.dumps(raw_metadata),
                is_enabled=True,
            )
        ],
    )


def test_source_request_overrides_never_reach_upstream_model_raw() -> None:
    source = _overrides_source({"source_request_overrides": {"options": {"num_ctx": 32768}}})

    models = source_models_to_upstream_models([source])

    assert len(models) == 1
    assert "source_request_overrides" not in models[0].raw
    # Overrides stay available for operator-side request application.
    assert source_model_request_overrides(source, "llama3.1:8b") == {"options": {"num_ctx": 32768}}


def test_source_model_request_overrides_ignores_non_mapping_values() -> None:
    source = _overrides_source({"source_request_overrides": ["not", "a", "mapping"]})

    assert source_model_request_overrides(source, "llama3.1:8b") == {}
    assert source_model_request_overrides(source, "unknown-model") == {}


def test_source_model_supported_tool_types_defaults_to_empty() -> None:
    source = _overrides_source({})

    assert source_model_supported_tool_types(source, "llama3.1:8b") == frozenset()
    assert source_model_supported_tool_types(source, "unknown-model") == frozenset()


def test_source_model_supported_tool_types_honors_search_opt_in() -> None:
    source = _overrides_source({"supports_search_tool": True})

    supported = source_model_supported_tool_types(source, "llama3.1:8b")

    assert "web_search" in supported
    assert "web_search_preview" in supported


def test_source_model_supported_tool_types_includes_experimental_tools() -> None:
    source = _overrides_source({"experimental_supported_tools": ["custom", 42, {"type": "bad"}]})

    assert source_model_supported_tool_types(source, "llama3.1:8b") == frozenset({"custom"})


def test_source_models_to_upstream_models_skips_disabled_sources_and_models() -> None:
    disabled_source = ModelSource(
        id="src_disabled",
        name="Disabled",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8000/v1",
        is_enabled=False,
        models=[ModelSourceModel(model="disabled-source-model", is_enabled=True)],
    )
    enabled_source = ModelSource(
        id="src_enabled",
        name="Enabled",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8001/v1",
        is_enabled=True,
        models=[ModelSourceModel(model="disabled-model", is_enabled=False)],
    )

    assert source_models_to_upstream_models([disabled_source, enabled_source]) == []


def test_source_models_force_codex_lb_provider_metadata() -> None:
    source = ModelSource(
        id="src_deepseek",
        name="DeepSeek",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="https://api.deepseek.example/v1",
        is_enabled=True,
        models=[
            ModelSourceModel(
                model="deepseek-v4-flash",
                raw_metadata_json=json.dumps({"model_provider": "deepseek"}),
                is_enabled=True,
            )
        ],
    )

    models = source_models_to_upstream_models([source])

    assert len(models) == 1
    assert models[0].raw["model_provider"] == "codex-lb"


def _reasoning_source(raw_metadata_json: str | None) -> ModelSource:
    return ModelSource(
        id="src_reasoning",
        name="Reasoning",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8000/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        supports_audio_transcriptions=False,
        models=[
            ModelSourceModel(
                model="reasoning-model",
                is_enabled=True,
                supports_streaming=True,
                raw_metadata_json=raw_metadata_json,
            )
        ],
    )


def test_source_model_without_metadata_advertises_no_reasoning_levels() -> None:
    [model] = source_models_to_upstream_models([_reasoning_source(None)])
    assert model.supported_reasoning_levels == ()
    assert model.default_reasoning_level is None
    assert model.supports_reasoning_summaries is False


def test_source_model_reasoning_levels_accept_effort_slugs() -> None:
    raw = json.dumps(
        {
            "supported_reasoning_levels": ["low", "medium", "high", "xhigh"],
            "default_reasoning_level": "high",
        }
    )
    [model] = source_models_to_upstream_models([_reasoning_source(raw)])
    assert [level.effort for level in model.supported_reasoning_levels] == [
        "low",
        "medium",
        "high",
        "xhigh",
    ]
    assert model.default_reasoning_level == "high"


def test_source_model_reasoning_levels_accept_objects_and_summaries() -> None:
    raw = json.dumps(
        {
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Low effort"},
                {"effort": "max", "description": "Max effort"},
            ],
            "default_reasoning_level": "max",
            "supports_reasoning_summaries": True,
        }
    )
    [model] = source_models_to_upstream_models([_reasoning_source(raw)])
    assert [(level.effort, level.description) for level in model.supported_reasoning_levels] == [
        ("low", "Low effort"),
        ("max", "Max effort"),
    ]
    assert model.default_reasoning_level == "max"
    assert model.supports_reasoning_summaries is True


def test_source_model_reasoning_levels_ignore_invalid_entries_and_defaults() -> None:
    raw = json.dumps(
        {
            "supported_reasoning_levels": ["low", "low", {"description": "no effort key"}, 7, {"effort": "high"}],
            # Not one of the advertised efforts, so it must not be surfaced.
            "default_reasoning_level": "ultra",
        }
    )
    [model] = source_models_to_upstream_models([_reasoning_source(raw)])
    assert [level.effort for level in model.supported_reasoning_levels] == ["low", "high"]
    assert model.default_reasoning_level is None


def test_source_model_reasoning_levels_ignore_non_list_metadata() -> None:
    raw = json.dumps({"supported_reasoning_levels": "high"})
    [model] = source_models_to_upstream_models([_reasoning_source(raw)])
    assert model.supported_reasoning_levels == ()


def test_source_model_reasoning_levels_are_normalized_and_clamped() -> None:
    """Declared efforts are advertised verbatim in a catalog clients parse as a
    whole, so an unknown effort must be dropped rather than forwarded."""
    raw = json.dumps(
        {
            "supported_reasoning_levels": [" Low ", "HIGH", "turbo", "midium", "low"],
            "default_reasoning_level": " HIGH ",
        }
    )
    [model] = source_models_to_upstream_models([_reasoning_source(raw)])
    assert [level.effort for level in model.supported_reasoning_levels] == ["low", "high"]
    assert model.default_reasoning_level == "high"


def test_source_model_default_level_outside_declared_set_is_dropped() -> None:
    raw = json.dumps({"supported_reasoning_levels": ["low"], "default_reasoning_level": "max"})
    [model] = source_models_to_upstream_models([_reasoning_source(raw)])
    assert model.default_reasoning_level is None


def test_declared_levels_imply_the_chat_path_reasoning_opt_in() -> None:
    """Advertising efforts on /v1/models while the chat sanitizer strips them
    would make the capability visible and inert at the same time."""
    raw = json.dumps({"supported_reasoning_levels": ["low", "high"]})
    source = _reasoning_source(raw)
    assert source_model_supports_reasoning(source, "reasoning-model") is True


def test_no_declared_levels_keeps_the_explicit_reasoning_opt_in() -> None:
    assert source_model_supports_reasoning(_reasoning_source(None), "reasoning-model") is False
    explicit = _reasoning_source(json.dumps({"supports_reasoning": True}))
    assert source_model_supports_reasoning(explicit, "reasoning-model") is True


def test_source_model_reasoning_levels_accessor_matches_the_catalog() -> None:
    raw = json.dumps({"supported_reasoning_levels": ["minimal", "low"]})
    source = _reasoning_source(raw)
    assert [level.effort for level in source_model_reasoning_levels(source, "reasoning-model")] == [
        "minimal",
        "low",
    ]
    assert source_model_reasoning_levels(source, "unknown-model") == ()


def test_declared_summaries_imply_the_chat_path_reasoning_opt_in() -> None:
    """``supports_reasoning_summaries`` is surfaced as ``supports_reasoning`` on
    /v1/models, so declaring it alone must not leave the chat path stripping."""
    summaries_only = _reasoning_source(json.dumps({"supports_reasoning_summaries": True}))
    assert source_model_supports_reasoning(summaries_only, "reasoning-model") is True


def test_declared_capability_outranks_an_explicit_false() -> None:
    """A declared capability wins over ``"supports_reasoning": false``.

    /v1/models derives ``supports_reasoning`` from the declared levels and the
    summary flag before consulting the raw key, so honoring the veto here alone
    would advertise the capability while the chat path strips it.
    """
    vetoed_levels = _reasoning_source(json.dumps({"supported_reasoning_levels": ["low"], "supports_reasoning": False}))
    assert source_model_supports_reasoning(vetoed_levels, "reasoning-model") is True
    vetoed_summaries = _reasoning_source(
        json.dumps({"supports_reasoning_summaries": True, "supports_reasoning": False})
    )
    assert source_model_supports_reasoning(vetoed_summaries, "reasoning-model") is True
