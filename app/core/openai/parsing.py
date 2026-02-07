from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from app.core.openai.models import OpenAIError, OpenAIErrorEnvelope, OpenAIEvent, OpenAIResponsePayload
from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json

_EVENT_ADAPTER = TypeAdapter(OpenAIEvent)
_ERROR_ADAPTER = TypeAdapter(OpenAIErrorEnvelope)
_RESPONSE_ADAPTER = TypeAdapter(OpenAIResponsePayload)


def parse_sse_event(line: str) -> OpenAIEvent | None:
    payload = parse_sse_data_json(line)
    if payload is None:
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
