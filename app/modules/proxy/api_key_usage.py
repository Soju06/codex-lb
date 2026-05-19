from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel

from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
    ApiKeyRequestUsageBudget,
)

_OPAQUE_INPUT_ITEM_TYPES = frozenset({"input_file", "input_image"})
_INPUT_BUDGET_EXCLUDED_FIELDS = frozenset(
    {
        "model",
        "service_tier",
        "stream",
        "store",
        "max_output_tokens",
        "max_completion_tokens",
        "max_tokens",
    }
)


def estimate_api_key_request_usage(payload: object) -> ApiKeyRequestUsageBudget:
    """Return a bounded local usage budget for API-key reservation admission.

    ``None`` means the proxy cannot size that side of the request locally, so
    API-key enforcement should use its conservative default for that dimension.
    """

    return ApiKeyRequestUsageBudget(
        input_tokens=_estimate_request_input_tokens(payload),
        output_tokens=None,
    )


def _estimate_request_input_tokens(payload: object) -> int | None:
    if _payload_field(payload, "previous_response_id") is not None:
        return None
    if _payload_field(payload, "conversation") is not None:
        return None
    if _contains_opaque_input_reference(_payload_field(payload, "input")):
        return None

    data = _payload_mapping(payload)
    for field in _INPUT_BUDGET_EXCLUDED_FIELDS:
        data.pop(field, None)
    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    if not serialized:
        return 0
    return min(len(serialized.encode("utf-8")), API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET)



def _payload_field(payload: object, field: str) -> object:
    if isinstance(payload, Mapping):
        mapping = cast(Mapping[str, object], payload)
        return mapping.get(field)
    extra = getattr(payload, "model_extra", None)
    if isinstance(extra, Mapping):
        extra_mapping = cast(Mapping[str, object], extra)
        if field in extra_mapping:
            return extra_mapping[field]
    return getattr(payload, field, None)


def _payload_mapping(payload: object) -> dict[str, Any]:
    if isinstance(payload, BaseModel):
        return dict(payload.model_dump(mode="json", exclude_none=True))
    if isinstance(payload, Mapping):
        mapping = cast(Mapping[object, Any], payload)
        return {str(key): value for key, value in mapping.items()}
    data: dict[str, Any] = {}
    for field in ("instructions", "input", "tools", "tool_choice", "reasoning", "text"):
        value = _payload_field(payload, field)
        if value is not None:
            data[field] = value
    return data


def _contains_opaque_input_reference(value: object) -> bool:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, object], value)
        item_type = mapping.get("type")
        if isinstance(item_type, str) and item_type in _OPAQUE_INPUT_ITEM_TYPES:
            return True
        if "file_id" in mapping:
            return True
        return any(_contains_opaque_input_reference(child) for child in mapping.values())
    if isinstance(value, list | tuple):
        return any(_contains_opaque_input_reference(item) for item in value)
    return False
