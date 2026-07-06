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
    # source_kind/source_id stay on the UpstreamModel fields only: raw is
    # copied into client-visible payloads (codex models "extra"), and internal
    # source identifiers must not leak to proxy clients.
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


def source_model_supports_reasoning(source: ModelSource, model: str) -> bool:
    """Whether the source model opted into reasoning via raw catalog metadata.

    Source catalog entries have no first-class reasoning flag; a model that
    genuinely supports reasoning can opt in with ``"supports_reasoning": true``
    in ``raw_metadata_json``. Everything else is treated as non-reasoning so
    client-sent reasoning toggles are stripped before forwarding.
    """
    entry = next(
        (candidate for candidate in source.models if candidate.model == model and candidate.is_enabled),
        None,
    )
    if entry is None:
        return False
    return _raw_metadata(entry).get("supports_reasoning") is True


def source_model_cost_usd(
    source: ModelSource,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float | None:
    """Price usage against the source's per-model rates.

    Returns ``None`` when the source has no catalog entry for the model or
    the entry declares no pricing, so callers can fall back to their default
    cost handling. Mirrors the subscription pricing semantics: cached input
    tokens are billed at the cached rate and subtracted from billable input.
    """
    entry = next(
        (candidate for candidate in source.models if candidate.model == model and candidate.is_enabled),
        None,
    )
    if entry is None:
        return None
    if entry.input_per_1m is None and entry.cached_input_per_1m is None and entry.output_per_1m is None:
        return None
    input_rate = entry.input_per_1m or 0.0
    cached_rate = entry.cached_input_per_1m if entry.cached_input_per_1m is not None else input_rate
    output_rate = entry.output_per_1m or 0.0
    billable_input = max(0, input_tokens - cached_input_tokens)
    return (
        (billable_input / 1_000_000) * input_rate
        + (cached_input_tokens / 1_000_000) * cached_rate
        + (output_tokens / 1_000_000) * output_rate
    )


def source_model_audio_cost_usd(source: ModelSource, model: str, audio_seconds: float) -> float | None:
    """Price transcribed audio against the source model's per-minute rate.

    Returns ``None`` when the model has no ``audio_per_minute`` rate so the
    caller can fall back to token pricing (or fail closed for limited keys).
    """
    entry = next(
        (candidate for candidate in source.models if candidate.model == model and candidate.is_enabled),
        None,
    )
    if entry is None or entry.audio_per_minute is None:
        return None
    if audio_seconds <= 0:
        return 0.0
    return (audio_seconds / 60.0) * entry.audio_per_minute


def _raw_metadata(source_model: ModelSourceModel) -> dict[str, JsonValue]:
    if source_model.raw_metadata_json is None:
        return {}
    parsed = json.loads(source_model.raw_metadata_json)
    return parsed if isinstance(parsed, dict) else {}
