from __future__ import annotations

from pydantic import TypeAdapter, ValidationError

from app.core.openai.models import (
    CompactResponsePayload,
    OpenAIError,
    OpenAIErrorEnvelope,
    OpenAIEvent,
    OpenAIResponsePayload,
)
from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json

_EVENT_ADAPTER = TypeAdapter(OpenAIEvent)
_ERROR_ADAPTER = TypeAdapter(OpenAIErrorEnvelope)
_RESPONSE_ADAPTER = TypeAdapter(OpenAIResponsePayload)
_COMPACT_RESPONSE_ADAPTER = TypeAdapter(CompactResponsePayload)

# Stream lifecycle frames are the only events whose validated model fields the
# proxy consumes (usage settlement, error normalization, response-id capture).
# Hot streaming paths validate only these frames and classify everything else
# from the already-parsed payload dict via ``classify_event_type``.
_LIFECYCLE_EVENT_TYPES = frozenset(
    {
        "response.created",
        "response.completed",
        "response.incomplete",
        "response.failed",
        "error",
    }
)


def classify_event_type(payload: JsonValue | None) -> str | None:
    """Classify an SSE event type from an already-parsed payload dict.

    Mirrors the dict branch of the proxy's ``_event_type_from_payload``:
    a string ``type`` field wins; a typeless payload carrying a dict
    ``error`` classifies as ``"error"``. No pydantic validation is run.
    """
    if not isinstance(payload, dict):
        return None
    payload_type = payload.get("type")
    if isinstance(payload_type, str):
        return payload_type
    if isinstance(payload.get("error"), dict):
        return "error"
    return None


def parse_sse_event(line: str) -> OpenAIEvent | None:
    return parse_sse_event_payload(parse_sse_data_json(line))


def parse_sse_event_payload(payload: JsonValue | None) -> OpenAIEvent | None:
    """Validate an already-parsed SSE data payload.

    Hot streaming paths parse each event's JSON exactly once and reuse the
    payload here instead of re-parsing the raw line per consumer.
    """
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


def parse_compact_response_payload(payload: JsonValue) -> CompactResponsePayload | None:
    if not isinstance(payload, dict):
        return None
    try:
        return _COMPACT_RESPONSE_ADAPTER.validate_python(payload)
    except ValidationError:
        return None
