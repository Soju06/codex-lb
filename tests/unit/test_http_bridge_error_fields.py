from __future__ import annotations

import pytest

from app.core.types import JsonValue
from app.modules.proxy._service.http_bridge.error_fields import (
    _parse_http_bridge_error_fields,
)


@pytest.mark.parametrize(
    ("param_present", "param", "expected_normalized_param", "expected_param_malformed"),
    [
        pytest.param(False, None, None, False, id="absent"),
        pytest.param(True, "previous_response_id", "previous_response_id", False, id="string"),
        pytest.param(True, "  previous_response_id  ", "previous_response_id", False, id="trimmed-string"),
        pytest.param(True, "", "", True, id="empty-string"),
        pytest.param(True, "   ", "", True, id="blank-string"),
        pytest.param(True, None, None, True, id="null"),
        pytest.param(True, 0, None, True, id="zero"),
        pytest.param(True, False, None, True, id="false"),
        pytest.param(True, {}, None, True, id="object"),
        pytest.param(True, [], None, True, id="array"),
    ],
)
def test_parser_preserves_parameter_presence_and_raw_value(
    param_present: bool,
    param: JsonValue,
    expected_normalized_param: str | None,
    expected_param_malformed: bool,
) -> None:
    error: dict[str, JsonValue] = {
        "code": " invalid_request_error ",
        "type": " invalid_request_error ",
        "message": " Invalid previous_response_id. ",
    }
    if param_present:
        error["param"] = param

    fields = _parse_http_bridge_error_fields({"error": error})

    assert fields is not None
    assert fields.normalized_code == "invalid_request_error"
    assert fields.message == "Invalid previous_response_id."
    assert fields.param_present is param_present
    assert fields.param == param
    assert fields.normalized_param == expected_normalized_param
    assert fields.param_malformed is expected_param_malformed


def test_parser_uses_direct_error_fields_from_top_level_error_event() -> None:
    fields = _parse_http_bridge_error_fields(
        {
            "type": "error",
            "code": "previous_response_not_found",
            "message": "Previous response was not found.",
            "param": "previous_response_id",
        }
    )

    assert fields is not None
    assert fields.normalized_code == "previous_response_not_found"
    assert fields.param_present is True
    assert fields.normalized_param == "previous_response_id"


def test_parser_uses_error_type_as_the_upstream_type_on_a_top_level_error_frame() -> None:
    """``type: "error"`` is the event discriminator, never the upstream type."""
    fields = _parse_http_bridge_error_fields(
        {
            "type": "error",
            "error_type": " invalid_request_error ",
            "code": "previous_response_not_found",
            "message": "Previous response was not found.",
            "param": "previous_response_id",
        }
    )

    assert fields is not None
    assert fields.type == "invalid_request_error"
    assert fields.code == "previous_response_not_found"
    assert fields.normalized_code == "previous_response_not_found"
    assert fields.normalized_param == "previous_response_id"


def test_parser_top_level_error_frame_without_error_type_has_no_type() -> None:
    fields = _parse_http_bridge_error_fields({"type": "error", "message": "Upstream error."})

    assert fields is not None
    assert fields.type is None
    assert fields.normalized_code == "upstream_error"


def test_parser_keeps_nested_detail_type_for_a_top_level_error_frame() -> None:
    """A nested ``error`` detail still owns its own ``type``."""
    fields = _parse_http_bridge_error_fields(
        {
            "type": "error",
            "error_type": "server_error",
            "error": {"type": "invalid_request_error", "code": "previous_response_not_found"},
        }
    )

    assert fields is not None
    assert fields.type == "invalid_request_error"


def test_parser_reads_response_failed_error_detail() -> None:
    fields = _parse_http_bridge_error_fields(
        {
            "type": "response.failed",
            "response": {
                "error": {
                    "code": "previous_response_not_found",
                    "message": "Previous response was not found.",
                }
            },
        }
    )

    assert fields is not None
    assert fields.normalized_code == "previous_response_not_found"
    assert fields.param_present is False
    assert fields.param is None


def test_parser_rejects_payload_without_error_detail() -> None:
    assert _parse_http_bridge_error_fields({"type": "response.completed"}) is None
