from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope, OpenAIEvent, OpenAIResponsePayload
from app.core.types import JsonValue

_EVENT_ADAPTER = TypeAdapter(OpenAIEvent)
_ERROR_ADAPTER = TypeAdapter(OpenAIErrorEnvelope)
_RESPONSE_ADAPTER = TypeAdapter(OpenAIResponsePayload)


def parse_sse_event(line: str) -> OpenAIEvent | None:
    data = None
    if line.startswith("data:"):
        data = line[5:].strip()
    elif "\n" in line:
        for part in line.splitlines():
            if part.startswith("data:"):
                data = part[5:].strip()
                break
    if data is None:
        return None
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return _EVENT_ADAPTER.validate_python(payload)
    except ValidationError:
        return None


def parse_error_payload(payload: JsonValue) -> OpenAIError | None:
    if not isinstance(payload, dict):
        return None
    try:
        envelope = _ERROR_ADAPTER.validate_python(payload)
    except ValidationError:
        return None
    return envelope.error


def parse_response_payload(payload: JsonValue) -> OpenAIResponsePayload | None:
    if not isinstance(payload, dict):
        return None
    try:
        return _RESPONSE_ADAPTER.validate_python(payload)
    except ValidationError:
        return None
