from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.model_registry import ModelRegistry
from app.core.openai.requests import ResponsesReasoning, ResponsesRequest
from app.core.types import JsonValue
from app.db.sqlite_utils import sqlite_db_path_from_url

logger = logging.getLogger(__name__)

_VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max", "maximum"})
_EFFORT_RANK = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "xhigh": 3,
    "max": 4,
}
_UNKNOWN_MODEL_MAXIMUM_FALLBACK = "high"


@dataclass(frozen=True, slots=True)
class OfficeAIReasoningControl:
    enabled: bool
    effort: str
    api_key_prefix: str | None = None


def default_officeai_reasoning_control_path(database_url: str) -> Path | None:
    database_path = sqlite_db_path_from_url(database_url)
    if database_path is None:
        return None
    return database_path.parent / "officeai-reasoning.json"


def load_officeai_reasoning_control(path: Path | None) -> OfficeAIReasoningControl | None:
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("version", 1) != 1:
        return None

    enabled = raw.get("enabled", True)
    effort = raw.get("effort", "maximum")
    api_key_prefix = raw.get("api_key_prefix")
    if not isinstance(enabled, bool) or not isinstance(effort, str):
        return None

    normalized_effort = effort.strip().lower()
    if normalized_effort not in _VALID_EFFORTS:
        return None
    if api_key_prefix is not None:
        if not isinstance(api_key_prefix, str):
            return None
        api_key_prefix = api_key_prefix.strip() or None

    return OfficeAIReasoningControl(
        enabled=enabled,
        effort=normalized_effort,
        api_key_prefix=api_key_prefix,
    )


def resolve_officeai_reasoning_effort(
    requested_effort: str,
    model: str,
    *,
    registry: ModelRegistry,
) -> str:
    normalized_requested = requested_effort.strip().lower()
    model_key = model.strip().lower()
    upstream = registry.get_models_with_fallback().get(model) or registry.get_models_with_fallback().get(model_key)
    if upstream is None or not upstream.supported_reasoning_levels:
        if normalized_requested == "maximum":
            return _UNKNOWN_MODEL_MAXIMUM_FALLBACK
        return normalized_requested if normalized_requested in _EFFORT_RANK else _UNKNOWN_MODEL_MAXIMUM_FALLBACK

    advertised: list[str] = []
    for level in upstream.supported_reasoning_levels:
        effort = level.effort.strip().lower()
        if effort == "ultra":
            effort = "max"
        if effort in _EFFORT_RANK and effort not in advertised:
            advertised.append(effort)
    if not advertised:
        return _UNKNOWN_MODEL_MAXIMUM_FALLBACK
    if normalized_requested == "maximum":
        return max(advertised, key=_EFFORT_RANK.__getitem__)

    requested_rank = _EFFORT_RANK.get(normalized_requested)
    if requested_rank is None:
        return _UNKNOWN_MODEL_MAXIMUM_FALLBACK
    at_or_below = [effort for effort in advertised if _EFFORT_RANK[effort] <= requested_rank]
    if at_or_below:
        return max(at_or_below, key=_EFFORT_RANK.__getitem__)
    return min(advertised, key=_EFFORT_RANK.__getitem__)


def apply_officeai_reasoning_override(
    payload: ResponsesRequest,
    *,
    original_chat_request: ChatCompletionsRequest,
    config_path: Path | None,
    authenticated_api_key_prefix: str | None,
    registry: ModelRegistry,
) -> str | None:
    control = load_officeai_reasoning_control(config_path)
    if control is None or not control.enabled:
        return None
    if control.api_key_prefix is not None and control.api_key_prefix != authenticated_api_key_prefix:
        return None
    if _has_explicit_reasoning_effort(original_chat_request):
        return None

    effort = resolve_officeai_reasoning_effort(
        control.effort,
        payload.model,
        registry=registry,
    )
    if payload.reasoning is None:
        payload.reasoning = ResponsesReasoning(effort=effort)
    else:
        payload.reasoning.effort = effort
    setattr(original_chat_request, "reasoning_effort", effort)
    logger.info(
        "officeai_reasoning_effort_applied model=%s effort=%s",
        payload.model,
        effort,
    )
    return effort


def _has_explicit_reasoning_effort(payload: ChatCompletionsRequest) -> bool:
    extra: Mapping[str, JsonValue] = payload.model_extra or {}
    for key in ("reasoning_effort", "reasoningEffort"):
        value = extra.get(key)
        if isinstance(value, str) and value.strip():
            return True
    reasoning = extra.get("reasoning")
    if isinstance(reasoning, Mapping):
        effort = reasoning.get("effort")
        return isinstance(effort, str) and bool(effort.strip())
    return False
