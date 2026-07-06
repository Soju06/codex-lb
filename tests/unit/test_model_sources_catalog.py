from __future__ import annotations

import json

import pytest

from app.core.openai.model_registry import MODEL_SOURCE_KIND_OPENAI_COMPATIBLE
from app.db.models import ModelSource, ModelSourceModel
from app.modules.model_sources.catalog import (
    source_model_audio_cost_usd,
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
