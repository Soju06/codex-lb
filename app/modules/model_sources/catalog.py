from __future__ import annotations

import json

from app.core.openai.model_registry import (
    MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
    UpstreamModel,
)
from app.core.types import JsonValue
from app.db.models import ModelSource, ModelSourceModel


def source_models_to_upstream_models(sources: list[ModelSource]) -> list[UpstreamModel]:
    models: list[UpstreamModel] = []
    for source in sources:
        if not source.is_enabled:
            continue
        if source.kind != MODEL_SOURCE_KIND_OPENAI_COMPATIBLE:
            continue
        for source_model in source.models:
            if not source_model.is_enabled:
                continue
            models.append(_to_upstream_model(source, source_model))
    return models


def _to_upstream_model(source: ModelSource, source_model: ModelSourceModel) -> UpstreamModel:
    raw = _raw_metadata(source_model)
    raw.setdefault("visibility", "list")
    if source_model.max_output_tokens is not None:
        raw["max_output_tokens"] = source_model.max_output_tokens
    raw["supports_streaming"] = source_model.supports_streaming
    raw["source_kind"] = source.kind
    raw["source_id"] = source.id
    raw["model_provider"] = "codex-lb"

    input_modalities = ("text", "image") if source_model.supports_vision else ("text",)
    display_name = source_model.display_name or source_model.model
    return UpstreamModel(
        slug=source_model.model,
        display_name=display_name,
        description=display_name,
        context_window=source_model.context_window or 0,
        input_modalities=input_modalities,
        supported_reasoning_levels=(),
        default_reasoning_level=None,
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=source_model.supports_tools,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset(),
        source_kind=source.kind,
        source_id=source.id,
        raw=raw,
    )


def _raw_metadata(source_model: ModelSourceModel) -> dict[str, JsonValue]:
    if source_model.raw_metadata_json is None:
        return {}
    parsed = json.loads(source_model.raw_metadata_json)
    return parsed if isinstance(parsed, dict) else {}
