"""Local strict-mode JSON schema validation.

Exercises the validator in ``app.core.openai.strict_schema`` and the
``ResponsesRequest`` integration. Mirrors the upstream OpenAI Responses
API error shape so that clients (Graphiti, raw SDK callers, etc.) see the
same ``invalid_json_schema`` payload regardless of which endpoint they
hit.
"""

from __future__ import annotations

import pytest

from app.core.openai.chat_requests import ChatCompletionsRequest
from app.core.openai.exceptions import ClientPayloadError
from app.core.openai.strict_schema import validate_strict_json_schema
from app.modules.proxy.request_policy import (
    enforce_strict_text_format,
    normalize_responses_request_payload,
)


def test_strict_root_missing_additional_properties():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    violation = validate_strict_json_schema(schema, name="person", param="text.format.schema")
    assert violation is not None
    assert violation.code == "invalid_json_schema"
    assert violation.param == "text.format.schema"
    assert "context=()" in violation.message
    assert "additionalProperties" in violation.message
    assert "person" in violation.message


def test_strict_root_additional_properties_true_rejected():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": True,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "context=()" in violation.message


def test_strict_nested_missing_additional_properties():
    schema = {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        },
        "required": ["nodes"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="graph", param="text.format.schema")
    assert violation is not None
    assert "context=('properties', 'nodes', 'items')" in violation.message


def test_strict_dict_str_any_pattern_rejected():
    """Pydantic ``dict[str, Any]`` -> ``additionalProperties: true``."""

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "attributes": {"type": "object", "additionalProperties": True},
        },
        "required": ["name", "attributes"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="EntityWithAttrs", param="text.format.schema")
    assert violation is not None
    assert "context=('properties', 'attributes')" in violation.message


def test_strict_empty_schema_node_rejected():
    """``Any`` typed Pydantic field -> ``{}`` schema node."""

    schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "extra": {},
        },
        "required": ["name", "extra"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="withAny", param="text.format.schema")
    assert violation is not None
    assert "context=('properties', 'extra')" in violation.message
    assert "must have a 'type' key" in violation.message


def test_strict_valid_schema_passes():
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name", "age"],
        "additionalProperties": False,
    }
    assert validate_strict_json_schema(schema, name="person", param="text.format.schema") is None


def test_strict_required_must_list_every_property():
    """Strict mode requires every key in ``properties`` to appear in ``required``.

    Mirrors OpenAI's wording so callers see the same diagnostic regardless
    of whether the request hit the local pre-check or the upstream API.
    """

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}, "y": {"type": "integer"}},
        "required": ["x"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "required" in violation.message
    assert "'y'" in violation.message
    assert "context=()" in violation.message


def test_strict_required_empty_array_rejected():
    """Even an empty ``required`` is rejected when ``properties`` is non-empty."""

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": [],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "'x'" in violation.message


def test_strict_required_missing_array_rejected():
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="p", param="text.format.schema")
    assert violation is not None
    assert "'x'" in violation.message


def test_strict_combinator_recurses_into_branches():
    schema = {
        "type": "object",
        "properties": {
            "value": {
                "anyOf": [
                    {"type": "string"},
                    # Nested object missing additionalProperties: false
                    {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}},
                        "required": ["x"],
                    },
                ]
            }
        },
        "required": ["value"],
        "additionalProperties": False,
    }
    violation = validate_strict_json_schema(schema, name="union", param="text.format.schema")
    assert violation is not None
    assert "anyOf" in violation.message


def test_normalize_responses_payload_rejects_strict_violation():
    payload = {
        "model": "gpt-5.5",
        "instructions": "",
        "input": "hi",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "person",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            }
        },
    }
    with pytest.raises(ClientPayloadError) as exc_info:
        normalize_responses_request_payload(payload, openai_compat=False)
    err = exc_info.value
    assert err.code == "invalid_json_schema"
    assert err.error_type == "invalid_request_error"
    assert err.param == "text.format.schema"
    assert "person" in str(err)


def test_normalize_responses_payload_accepts_strict_false():
    payload = {
        "model": "gpt-5.5",
        "instructions": "",
        "input": "hi",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "person",
                "strict": False,
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            }
        },
    }
    # Strict=False schemas are passed through unchanged.
    request = normalize_responses_request_payload(payload, openai_compat=False)
    assert request.text is not None
    assert request.text.format is not None
    assert request.text.format.strict is False


def test_normalize_responses_payload_accepts_valid_strict_schema():
    payload = {
        "model": "gpt-5.5",
        "instructions": "",
        "input": "hi",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "person",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                    "required": ["name", "age"],
                    "additionalProperties": False,
                },
            }
        },
    }
    request = normalize_responses_request_payload(payload, openai_compat=False)
    assert request.text is not None
    assert request.text.format is not None
    assert request.text.format.strict is True


def test_chat_completions_strict_schema_violation_surfaces_via_enforce_helper():
    payload = {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "hi"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "person",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
    }
    request = ChatCompletionsRequest.model_validate(payload)
    responses_request = request.to_responses_request()
    with pytest.raises(ClientPayloadError) as exc_info:
        enforce_strict_text_format(responses_request)
    err = exc_info.value
    assert err.code == "invalid_json_schema"
    assert err.param == "text.format.schema"
