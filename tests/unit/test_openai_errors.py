from __future__ import annotations

from app.core.errors import (
    PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE,
    OpenAIErrorParam,
    coerce_error_param,
    is_previous_response_not_found_error,
    is_previous_response_not_found_public_shape,
    normalize_public_error_param,
    previous_response_id_from_not_found_message,
    previous_response_stream_incomplete_error,
    response_failed_event,
    sanitize_public_error_detail,
)

# Raw malformed values used to be collapsed to an empty-string sentinel by
# normalizers. The classifier now receives ``None`` as the legacy shorthand
# for an absent field; explicit null/non-string presence is covered by the
# presence-aware ``OpenAIErrorParam`` tests in the bridge suite.
_MALFORMED_PARAMS = ("", " ", "\t\n", 0, False, {}, [])


def test_response_failed_event_includes_incomplete_details():
    event = response_failed_event("stream_incomplete", "Upstream closed stream", response_id="resp_1")

    response = event["response"]
    assert "incomplete_details" in response
    assert response["incomplete_details"] is None


def test_response_failed_event_accepts_incomplete_details():
    event = response_failed_event(
        "stream_incomplete",
        "Upstream closed stream",
        response_id="resp_1",
        incomplete_details={"reason": "max_output_tokens"},
    )

    response = event["response"]
    assert response.get("incomplete_details") == {"reason": "max_output_tokens"}


def test_response_failed_event_preserves_reset_hint():
    event = response_failed_event(
        "usage_limit_reached",
        "Rate limit exceeded. Try again in 1h",
        error_type="usage_limit_reached",
        response_id="resp_1",
        resets_at=1_700_003_600,
    )

    assert event["response"]["error"].get("resets_at") == 1_700_003_600


def test_response_failed_event_omits_malformed_params():
    """No raw malformed value may cross this client-facing serializer."""
    for param in (OpenAIErrorParam.absent(), OpenAIErrorParam(True, None), *_MALFORMED_PARAMS):
        event = response_failed_event(
            "previous_response_not_found",
            "Previous response was not found.",
            error_type="invalid_request_error",
            response_id="resp_1",
            error_param=param,
        )

        error = event["response"]["error"]
        assert "param" not in error
        assert error.get("code") == "previous_response_not_found"
        assert error.get("type") == "invalid_request_error"
        assert error.get("message") == "Previous response was not found."


def test_response_failed_event_trims_canonical_param():
    for param in ("  previous_response_id  ", OpenAIErrorParam(True, "  previous_response_id  ")):
        event = response_failed_event(
            "previous_response_not_found",
            "Previous response was not found.",
            response_id="resp_1",
            error_param=param,
        )

        assert event["response"]["error"].get("param") == "previous_response_id"


def test_public_error_param_normalization_omits_malformed_values():
    for param in (None, 0, False, {}, [], "", "   ", "\t\n"):
        assert normalize_public_error_param(param) is None
        assert "param" not in sanitize_public_error_detail({"param": param})

    assert normalize_public_error_param("  previous_response_id  ") == "previous_response_id"
    assert sanitize_public_error_detail({"param": "  input  "}) == {"param": "input"}


def test_public_error_param_sanitization_does_not_change_internal_presence_state():
    malformed = OpenAIErrorParam(True, {})
    assert malformed.present is True
    assert malformed.malformed is True
    assert normalize_public_error_param(malformed) is None


def test_coerce_error_param_reuses_presence_aware_state():
    assert coerce_error_param(None) == OpenAIErrorParam.absent()
    assert coerce_error_param(" input ") == OpenAIErrorParam(True, " input ")

    explicit_null = OpenAIErrorParam(True, None)
    assert coerce_error_param(explicit_null) is explicit_null


def test_previous_response_not_found_classifier_covers_openai_shapes():
    assert is_previous_response_not_found_error(
        code="previous_response_not_found",
        param=None,
        message="Previous response with id 'resp_abc' not found.",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param="previous_response_id",
        message='Previous response with id "resp_abc" not found.',
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`.",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param="previous_response_id",
        message="Invalid `previous_response_id`.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param="input",
        message='Previous response with id "resp_abc" not found.',
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param="input",
        message="Invalid `previous_response_id`.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid request payload.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`...",
    )
    assert not is_previous_response_not_found_error(
        code=None,
        param=None,
        message="Invalid `previous_response_id`.",
    )


def test_previous_response_not_found_classifier_covers_parameterless_invalid_anchor():
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid `previous_response_id`.",
    )
    assert is_previous_response_not_found_error(
        code="invalid_request_error",
        param="previous_response_id",
        message="Invalid previous_response_id.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param="input",
        message="Invalid previous_response_id.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="Invalid input.",
    )
    assert not is_previous_response_not_found_error(
        code="invalid_request_error",
        param=None,
        message="A required tool output from the previous response was not found.",
    )


def test_previous_response_not_found_classifier_rejects_non_string_param():
    for param in (0, False, {}, []):
        assert not is_previous_response_not_found_error(
            code="invalid_request_error",
            param=param,
            message="Invalid previous_response_id.",
        )


def test_previous_response_not_found_classifier_rejects_malformed_canonical_param():
    for param in _MALFORMED_PARAMS:
        assert not is_previous_response_not_found_error(
            code="previous_response_not_found",
            param=param,
            message="Previous response was not found.",
        )


def test_public_shape_masks_canonical_code_regardless_of_malformed_param():
    for param in (None, "previous_response_id", *_MALFORMED_PARAMS):
        assert is_previous_response_not_found_public_shape(
            code="previous_response_not_found",
            param=param,
            message="Previous response was not found.",
        )


def test_recovery_classifier_keeps_noncanonical_malformed_params_fail_closed():
    for param in _MALFORMED_PARAMS:
        assert not is_previous_response_not_found_error(
            code="invalid_request_error",
            param=param,
            message="Invalid `previous_response_id`.",
        )


def test_public_shape_masks_noncanonical_malformed_params_with_stale_message():
    for param in _MALFORMED_PARAMS:
        assert is_previous_response_not_found_public_shape(
            code="invalid_request_error",
            param=param,
            message="Invalid `previous_response_id`.",
        )


def test_public_shape_masks_raw_previous_response_id_with_malformed_param():
    assert is_previous_response_not_found_public_shape(
        code="invalid_request_error",
        param={},
        message="Previous response with id 'resp_leaked' not found.",
    )


def test_public_shape_is_a_superset_of_the_recovery_classifier():
    cases = [
        ("previous_response_not_found", None, "Previous response was not found."),
        ("invalid_request_error", "previous_response_id", "Previous response was not found."),
        ("invalid_request_error", None, "Invalid `previous_response_id`."),
        ("invalid_request_error", "input", "No tool output found for function call call_1."),
        ("invalid_request_error", "input", "Previous response was not found."),
        ("rate_limit_exceeded", None, "Rate limit reached."),
        (None, None, None),
    ]
    for code, param, message in cases:
        if is_previous_response_not_found_error(code=code, param=param, message=message):
            assert is_previous_response_not_found_public_shape(code=code, param=param, message=message)


def test_public_shape_does_not_claim_unrelated_params():
    assert not is_previous_response_not_found_public_shape(
        code="invalid_request_error",
        param="input",
        message="No tool output found for function call call_1.",
    )
    assert not is_previous_response_not_found_public_shape(
        code="rate_limit_exceeded",
        param=None,
        message="Rate limit reached.",
    )


def test_previous_response_id_from_not_found_message_extracts_anchor():
    assert (
        previous_response_id_from_not_found_message(
            'Previous response with id "resp_0ba42212936dca97016a0d52aec2588191bc2499d3088e4e3e" not found.'
        )
        == "resp_0ba42212936dca97016a0d52aec2588191bc2499d3088e4e3e"
    )


def test_previous_response_stream_incomplete_error_is_public_safe():
    payload = previous_response_stream_incomplete_error()

    assert payload["error"].get("code") == "stream_incomplete"
    assert payload["error"].get("type") == "server_error"
    assert payload["error"].get("message") == PREVIOUS_RESPONSE_STREAM_INCOMPLETE_MESSAGE
