from __future__ import annotations

import json

from app.core.openai.model_registry import (
    MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
    ReasoningLevel,
    UpstreamModel,
)
from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping
from app.db.models import ModelSource, ModelSourceModel

DEFAULT_SOURCE_CONTEXT_WINDOW = 128_000

# ``web_search_preview`` is the legacy alias for ``web_search``; request
# validation normalizes it, but accept both here so operator opt-in covers
# payloads regardless of normalization order.
_SEARCH_TOOL_TYPES = frozenset({"web_search", "web_search_preview"})


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
    # Operator-side request override config is applied server-side at forwarding
    # time (see source_model_request_overrides); it must never reach the
    # client-visible catalog payloads built from UpstreamModel.raw.
    raw.pop("source_request_overrides", None)
    context_window = source_model.context_window or DEFAULT_SOURCE_CONTEXT_WINDOW
    raw.setdefault("visibility", "list")
    raw.setdefault("shell_type", "shell_command")
    raw.setdefault("max_context_window", context_window)
    raw.setdefault("truncation_policy", {"mode": "tokens", "limit": 10_000})
    raw.setdefault("include_skills_usage_instructions", False)
    raw.setdefault("supports_image_detail_original", False)
    raw.setdefault("supports_search_tool", False)
    raw.setdefault("use_responses_lite", False)
    raw.setdefault("experimental_supported_tools", [])
    if source_model.max_output_tokens is not None:
        raw["max_output_tokens"] = source_model.max_output_tokens
    raw["supports_streaming"] = source_model.supports_streaming
    # source_kind/source_id stay on the UpstreamModel fields only: raw is
    # copied into client-visible payloads (codex models "extra"), and internal
    # source identifiers must not leak to proxy clients.
    raw["model_provider"] = "codex-lb"

    input_modalities = ("text", "image") if source_model.supports_vision else ("text",)
    display_name = source_model.display_name or source_model.model
    # The dashboard's single Reasoning switch is the master gate: it is the
    # only reasoning control an operator has in the UI, so a model with it off
    # must not advertise efforts it will never be allowed to use. Keeping the
    # switch authoritative is what lets the Codex catalog, /v1/models and the
    # dashboard checkbox agree; deriving levels regardless would advertise a
    # capability the chat sanitizer then strips.
    reasoning_opted_in = raw.get("supports_reasoning") is True
    reasoning_levels = _reasoning_levels_from_metadata(raw) if reasoning_opted_in else ()
    default_reasoning_level = (
        _default_reasoning_level_from_metadata(raw, reasoning_levels) if reasoning_opted_in else None
    )
    return UpstreamModel(
        slug=source_model.model,
        display_name=display_name,
        description=display_name,
        context_window=context_window,
        input_modalities=input_modalities,
        supported_reasoning_levels=reasoning_levels,
        default_reasoning_level=default_reasoning_level,
        supports_reasoning_summaries=reasoning_opted_in and raw.get("supports_reasoning_summaries") is True,
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


def _reasoning_levels_from_metadata(raw: dict[str, JsonValue]) -> tuple[ReasoningLevel, ...]:
    """Reasoning efforts advertised for a source model.

    Source catalogs have no first-class reasoning schema, so operators declare
    the efforts their backend accepts under ``supported_reasoning_levels`` in
    ``raw_metadata_json``. Both shapes are accepted::

        ["low", "high", "max"]
        [{"effort": "low", "description": "Low reasoning effort"}]

    Efforts are normalized (trimmed and lowercased) and deduplicated.
    Validation is on shape, not on membership of a fixed vocabulary: backends
    disagree on which efforts exist (GLM exposes ``none``, Model Studio
    includes it, others stop at ``low``/``high``/``max``), so an enum here
    would silently drop efforts a provider really accepts. Malformed entries --
    a non-string, a mapping without a string ``effort``, an empty slug -- are
    ignored, keeping the previous no-reasoning default for models that never
    opted in.
    """
    declared = raw.get("supported_reasoning_levels")
    if not is_json_list(declared):
        return ()
    levels: list[ReasoningLevel] = []
    seen: set[str] = set()
    for item in declared:
        if isinstance(item, str):
            effort = item
            description = f"{item.strip().lower()} reasoning effort"
        elif is_json_mapping(item):
            effort_value = item.get("effort")
            if not isinstance(effort_value, str):
                continue
            effort = effort_value
            description_value = item.get("description")
            description = (
                description_value
                if isinstance(description_value, str)
                else f"{effort.strip().lower()} reasoning effort"
            )
        else:
            continue
        effort = effort.strip().lower()
        if not effort or effort in seen:
            continue
        seen.add(effort)
        levels.append(ReasoningLevel(effort=effort, description=description))
    return tuple(levels)


def _default_reasoning_level_from_metadata(
    raw: dict[str, JsonValue],
    levels: tuple[ReasoningLevel, ...],
) -> str | None:
    """Operator-declared default effort, restricted to the advertised levels."""
    declared = raw.get("default_reasoning_level")
    if not isinstance(declared, str):
        return None
    normalized = declared.strip().lower()
    if not any(level.effort == normalized for level in levels):
        return None
    return normalized


def _enabled_source_model(source: ModelSource, model: str) -> ModelSourceModel | None:
    return next(
        (candidate for candidate in source.models if candidate.model == model and candidate.is_enabled),
        None,
    )


def source_model_reasoning_levels(source: ModelSource, model: str) -> tuple[ReasoningLevel, ...]:
    """Reasoning efforts an opted-in source model declared.

    Gated on ``supports_reasoning`` like the catalog derivation, so the
    unsupported-effort restore cannot hand a declared effort to a model whose
    operator left the Reasoning switch off.
    """
    entry = _enabled_source_model(source, model)
    if entry is None:
        return ()
    raw = _raw_metadata(entry)
    if raw.get("supports_reasoning") is not True:
        return ()
    return _reasoning_levels_from_metadata(raw)


def source_model_supports_reasoning(source: ModelSource, model: str) -> bool:
    """Whether the operator turned the model's Reasoning switch on.

    ``"supports_reasoning": true`` in ``raw_metadata_json`` is the single
    opt-in, written by the dashboard's Reasoning checkbox. Declared levels do
    not imply it: they describe *which* efforts an opted-in backend accepts,
    not *whether* reasoning is allowed at all, and the catalog derivation is
    gated on this same flag so the two can never disagree. Everything else is
    treated as non-reasoning, so client-sent reasoning toggles are stripped
    before forwarding on the chat path.
    """
    entry = _enabled_source_model(source, model)
    if entry is None:
        return False
    return _raw_metadata(entry).get("supports_reasoning") is True


def source_model_request_overrides(source: ModelSource, model: str) -> dict[str, JsonValue]:
    """Operator-configured request overrides for a source model.

    Overrides live under ``"source_request_overrides"`` in
    ``raw_metadata_json`` and are applied server-side when forwarding; they are
    stripped from the client-visible catalog metadata (see
    ``_to_upstream_model``).
    """
    entry = next(
        (candidate for candidate in source.models if candidate.model == model and candidate.is_enabled),
        None,
    )
    if entry is None:
        return {}
    value = _raw_metadata(entry).get("source_request_overrides")
    if not is_json_mapping(value):
        return {}
    return dict(value)


def source_model_supported_tool_types(source: ModelSource, model: str) -> frozenset[str]:
    """Non-function Responses tool types the source model declares support for.

    Function tools are always forwarded to OpenAI-compatible sources; hosted
    tool types are dropped unless the model opts in via
    ``"supports_search_tool": true`` (web search) or lists the tool type in
    ``"experimental_supported_tools"`` in ``raw_metadata_json``.
    """
    entry = next(
        (candidate for candidate in source.models if candidate.model == model and candidate.is_enabled),
        None,
    )
    if entry is None:
        return frozenset()
    raw = _raw_metadata(entry)
    supported: set[str] = set()
    if raw.get("supports_search_tool") is True:
        supported |= _SEARCH_TOOL_TYPES
    experimental = raw.get("experimental_supported_tools")
    if is_json_list(experimental):
        supported.update(item for item in experimental if isinstance(item, str))
    return frozenset(supported)


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
