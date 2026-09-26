"""Model identity translation at the source transport boundary."""

from __future__ import annotations

import json
import re

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping
from app.core.utils.sse import parse_sse_data_json
from app.db.models import ModelSource
from app.modules.model_sources.catalog import source_model_upstream_id


def source_request_payload(source: ModelSource, payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
    model = payload.get("model")
    if not isinstance(model, str):
        return payload
    upstream = source_model_upstream_id(source, model)
    return {**payload, "model": upstream} if upstream != model else payload


def restore_model_identity(payload: dict[str, JsonValue], *, model: str, upstream_model: str) -> bool:
    """Rewrite only protocol model fields, leaving text and tool arguments alone."""
    if model == upstream_model:
        return False
    changed = False
    if payload.get("model") == upstream_model:
        payload["model"] = model
        changed = True
    response = payload.get("response")
    if is_json_mapping(response) and response.get("model") == upstream_model:
        payload["response"] = {**response, "model": model}
        changed = True
    return changed


def source_response_payload(
    source: ModelSource, request: dict[str, JsonValue], response: dict[str, JsonValue]
) -> dict[str, JsonValue]:
    model = request.get("model")
    if isinstance(model, str):
        restore_model_identity(response, model=model, upstream_model=source_model_upstream_id(source, model))
    return response


def restore_sse_model_identity(block: str, *, model: str, upstream_model: str) -> str:
    parsed = parse_sse_data_json(block)
    if parsed is None:
        return block
    payload = dict(parsed)
    if not restore_model_identity(payload, model=model, upstream_model=upstream_model):
        return block
    # Retain event/id/retry/comment fields. Legal multiline data is replaced by
    # one JSON data line; do not split on Unicode separators inside JSON text.
    lines = re.split(r"\r\n|\r|\n", block)
    rewritten: list[str] = []
    written = False
    for line in lines:
        if line == "data" or line.startswith("data:"):
            if not written:
                rewritten.append("data: " + json.dumps(payload, ensure_ascii=True, separators=(",", ":")))
                written = True
        else:
            rewritten.append(line)
    return "\n".join(rewritten)
