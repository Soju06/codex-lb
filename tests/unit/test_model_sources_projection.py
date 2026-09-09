"""Source-body projection: telemetry stripping and the overflow portability view (#2123 WP-C1, §4.6, CP-7).

The two ``tests/fixtures/codex_bodies`` bodies are synthetic but shaped after
Codex's request construction (``client.rs``/``responses_metadata.rs`` for the
gpt-5.5 standard body; ``core/tests/suite/responses_lite.rs`` for the gpt-5.6
Responses-Lite bundle: ``namespace`` tools ``functions``->``exec``/``wait``,
``web``->``run``, ``image_gen``->``imagegen``, the tagged developer
base-instructions message, ``reasoning.context=all_turns``, no top-level
``tools``). Captured bodies replace them before WP-C2 (design §16 item i).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given, settings

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.model_sources.projection import (
    DECLINE_REASONS,
    OVERFLOW_VIEW_FIELDS,
    OVERFLOW_VIEW_REASONING_FIELDS,
    SERVICE_TIER_FIELD,
    STREAM_OPTIONS_FIELD,
    STRIPPED_STREAM_OPTIONS_KEYS,
    STRIPPED_TELEMETRY_FIELDS,
    Declined,
    PortabilityView,
    overflow_portability_view,
    strip_source_telemetry,
)
from tests.unit.hypothesis_strategies import json_objects

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex_bodies"


def _load_fixture(name: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _forwarded(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """The production dump the source dispatch strips (``payload.model_dump_for_forwarding()``)."""

    return ResponsesRequest.model_validate(body).model_dump_for_forwarding()


def _telemetry_payload() -> dict[str, JsonValue]:
    return {
        "model": "gpt-5.5",
        "instructions": "x",
        "input": [{"role": "user", "content": "hi"}],
        "tools": [{"type": "function", "name": "shell"}],
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": "cache-key-verbatim",
        "prompt_cache_retention": "24h",
        "background": False,
        "max_tool_calls": 3,
        "top_logprobs": 2,
        "service_tier": "priority",
        "client_metadata": {"x-codex-installation-id": "inst", "session_id": "s", "thread_id": "t"},
        "stream_options": {"reasoning_summary_delivery": "interleaved"},
        "access_programs": ["program"],
        "x_future_field": {"nested": [1, 2]},
    }


# --- strip_source_telemetry ---------------------------------------------------------


def test_strip_removes_exactly_the_telemetry_fields_in_place() -> None:
    payload = _telemetry_payload()
    before = copy.deepcopy(payload)

    result = strip_source_telemetry(payload)

    assert result is payload
    # ``client_metadata`` / ``access_programs`` whole; ``stream_options`` because
    # removing its only key (the Codex one) emptied it.
    assert (
        set(before) - set(result)
        == set(STRIPPED_TELEMETRY_FIELDS) | {STREAM_OPTIONS_FIELD}
        == {
            "client_metadata",
            "stream_options",
            "access_programs",
        }
    )
    for field, value in before.items():
        if field not in STRIPPED_TELEMETRY_FIELDS and field != STREAM_OPTIONS_FIELD:
            assert result[field] == value, field
    # ``prompt_cache_key`` verbatim, ``include`` intact (mutant: strip include),
    # ``service_tier`` kept for direct routing, unknown fields forwarded (CP-7).
    assert result["prompt_cache_key"] == "cache-key-verbatim"
    assert result["include"] == ["reasoning.encrypted_content"]
    assert result["service_tier"] == "priority"
    assert result["x_future_field"] == {"nested": [1, 2]}
    assert result["background"] is False and result["max_tool_calls"] == 3 and result["top_logprobs"] == 2


def test_strip_service_tier_only_when_flagged() -> None:
    kept = strip_source_telemetry(_telemetry_payload())
    assert kept[SERVICE_TIER_FIELD] == "priority"

    stripped = strip_source_telemetry(_telemetry_payload(), strip_service_tier=True)
    assert SERVICE_TIER_FIELD not in stripped
    assert set(_telemetry_payload()) - set(stripped) == set(STRIPPED_TELEMETRY_FIELDS) | {
        STREAM_OPTIONS_FIELD,
        SERVICE_TIER_FIELD,
    }


def test_strip_is_idempotent_and_tolerates_absent_fields() -> None:
    payload: dict[str, JsonValue] = {"model": "gpt-5.5", "input": "hello"}
    assert strip_source_telemetry(payload) == {"model": "gpt-5.5", "input": "hello"}
    assert strip_source_telemetry(payload, strip_service_tier=True) == {"model": "gpt-5.5", "input": "hello"}

    once = strip_source_telemetry(_telemetry_payload())
    twice = strip_source_telemetry(dict(once))
    assert once == twice


@pytest.mark.parametrize(
    ("stream_options", "expected"),
    [
        # Codex: the delivery mode is the only key, so the emptied object is dropped.
        ({"reasoning_summary_delivery": "interleaved"}, None),
        # SDK client alongside Codex telemetry: only the Codex key goes.
        ({"include_obfuscation": False, "reasoning_summary_delivery": "interleaved"}, {"include_obfuscation": False}),
        # Standard field alone: forwarded unchanged.
        ({"include_obfuscation": True}, {"include_obfuscation": True}),
        # Nothing removed, so nothing dropped: an already-empty object is the client's, not telemetry.
        ({}, {}),
        # Not an object: forwarded untouched for the source to judge (never fail closed).
        ("interleaved", "interleaved"),
        (["reasoning_summary_delivery"], ["reasoning_summary_delivery"]),
    ],
    ids=["codex_only", "sdk_plus_codex", "sdk_only", "empty", "string", "list"],
)
def test_strip_removes_only_the_codex_key_from_stream_options(stream_options: JsonValue, expected: JsonValue) -> None:
    """Design §4.6 names ``reasoning_summary_delivery`` as the telemetry; ``stream_options`` itself is a standard
    Responses field (``include_obfuscation``) that ``main`` forwarded verbatim (#2208 residual 1)."""

    payload: dict[str, JsonValue] = {"model": "gpt-5.5", "input": [], "stream_options": copy.deepcopy(stream_options)}
    nested_before = payload["stream_options"]

    stripped = strip_source_telemetry(payload)

    if expected is None:
        assert STREAM_OPTIONS_FIELD not in stripped
    else:
        assert stripped["stream_options"] == expected
        # In place: the client's own object is edited, not replaced.
        assert stripped["stream_options"] is nested_before
    assert stripped["model"] == "gpt-5.5" and stripped["input"] == []


def test_a_surviving_stream_options_forwards_for_direct_routing_but_declines_the_overflow_view() -> None:
    """The view allowlist (§4.6) is not widened: overflow stays Codex-shaped, direct routing forwards the field."""

    codex_shaped = {**_full_allowlisted_body(), "stream_options": {"reasoning_summary_delivery": "interleaved"}}
    assert overflow_portability_view(codex_shaped) == Declined("not_portable_unknown_field", "stream_options")
    assert isinstance(overflow_portability_view(strip_source_telemetry(codex_shaped)), PortabilityView)

    sdk_shaped = {**_full_allowlisted_body(), "stream_options": {"include_obfuscation": True}}
    stripped = strip_source_telemetry(sdk_shaped)
    assert stripped["stream_options"] == {"include_obfuscation": True}
    assert overflow_portability_view(stripped) == Declined("not_portable_unknown_field", "stream_options")


def test_strip_never_fails_closed_on_an_unknown_field_while_the_view_declines_it() -> None:
    """CP-7: the same unknown field forwards for direct routing and declines overflow."""

    stripped = strip_source_telemetry({"model": "gpt-5.5", "input": [], "x_future_field": 1})
    assert stripped["x_future_field"] == 1

    assert overflow_portability_view(stripped) == Declined("not_portable_unknown_field", "x_future_field")


# --- overflow_portability_view ------------------------------------------------------


def _full_allowlisted_body() -> dict[str, JsonValue]:
    return {
        "model": "gpt-5.5",
        "input": [{"role": "user", "content": "hi"}],
        "instructions": "base",
        "tools": [{"type": "function", "name": "shell"}],
        "tool_choice": "auto",
        "parallel_tool_calls": False,
        "reasoning": {"effort": "medium", "summary": "auto"},
        "text": {"verbosity": "medium"},
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "stream": True,
        "truncation": "auto",
        "max_output_tokens": 4096,
        "temperature": 1.0,
        "top_p": 1.0,
        "metadata": {"team": "a"},
        "user": "user-1",
        "safety_identifier": "safety-1",
        "prompt_cache_key": "cache-1",
        "prompt_cache_retention": "24h",
        "previous_response_id": "resp_1",
        "conversation": "conv_1",
        "prompt": {"id": "pmpt_1"},
    }


def test_view_admits_every_allowlisted_field_as_a_shallow_copy() -> None:
    body = _full_allowlisted_body()
    assert set(body) == set(OVERFLOW_VIEW_FIELDS)

    view = overflow_portability_view(body)

    assert isinstance(view, PortabilityView)
    assert dict(view.body) == body
    assert view.body is not body
    body["model"] = "mutated-later"
    assert view.body["model"] == "gpt-5.5"


def test_view_declines_unknown_top_level_fields_naming_every_offender_sorted() -> None:
    body = {**_full_allowlisted_body(), "zeta": 1, "alpha": {"nested": True}}

    assert overflow_portability_view(body) == Declined("not_portable_unknown_field", "alpha,zeta")


@pytest.mark.parametrize("telemetry_field", sorted(STRIPPED_TELEMETRY_FIELDS) + [SERVICE_TIER_FIELD])
def test_view_is_built_from_the_stripped_body_so_telemetry_is_an_unknown_field(telemetry_field: str) -> None:
    body = {**_full_allowlisted_body(), telemetry_field: {"k": "v"}}

    assert overflow_portability_view(body) == Declined("not_portable_unknown_field", telemetry_field)
    assert isinstance(overflow_portability_view(strip_source_telemetry(body, strip_service_tier=True)), PortabilityView)


@pytest.mark.parametrize(
    ("reasoning", "expected"),
    [
        ({"effort": "high", "summary": "detailed"}, None),
        ({"effort": "high"}, None),
        ({}, None),
        ({"effort": "high", "context": "all_turns"}, Declined("not_portable_lite_namespace", "reasoning.context")),
        (
            {"context": "all_turns", "budget": 1},
            Declined("not_portable_lite_namespace", "reasoning.budget,reasoning.context"),
        ),
        ("high", Declined("not_portable_unknown_field", "reasoning")),
        (["high"], Declined("not_portable_unknown_field", "reasoning")),
    ],
)
def test_view_reasoning_admits_effort_and_summary_only(reasoning: JsonValue, expected: Declined | None) -> None:
    body: dict[str, JsonValue] = {"model": "gpt-5.5", "input": [], "reasoning": reasoning}

    view = overflow_portability_view(body)

    if expected is None:
        assert isinstance(view, PortabilityView)
        assert view.body["reasoning"] == reasoning
    else:
        assert view == expected
    assert OVERFLOW_VIEW_REASONING_FIELDS == frozenset({"effort", "summary"})


def test_view_declines_the_lite_tool_bundle_before_any_history_check() -> None:
    body: dict[str, JsonValue] = {
        "model": "gpt-5.6-sol",
        "input": [
            {"type": "additional_tools", "role": "developer", "tools": [{"type": "custom", "name": "shell"}]},
            {"role": "user", "content": "hi"},
        ],
    }

    assert overflow_portability_view(body) == Declined("not_portable_lite_namespace", "additional_tools")


def test_view_treats_a_missing_or_string_input_as_portable_shape() -> None:
    assert isinstance(overflow_portability_view({"model": "gpt-5.5"}), PortabilityView)
    assert isinstance(overflow_portability_view({"model": "gpt-5.5", "input": "hello"}), PortabilityView)


@settings(max_examples=150, deadline=None)
@given(json_objects)
def test_view_never_raises_and_only_uses_closed_reasons(body: dict[str, JsonValue]) -> None:
    view = overflow_portability_view(body)

    if isinstance(view, Declined):
        assert view.reason in DECLINE_REASONS
        assert view.reason in {"not_portable_unknown_field", "not_portable_lite_namespace"}
    else:
        assert isinstance(view, PortabilityView)
        assert set(view.body) <= OVERFLOW_VIEW_FIELDS


def test_constants_are_disjoint_and_closed() -> None:
    assert STRIPPED_TELEMETRY_FIELDS == frozenset({"client_metadata", "access_programs"})
    assert STRIPPED_STREAM_OPTIONS_KEYS == frozenset({"reasoning_summary_delivery"})
    assert STREAM_OPTIONS_FIELD == "stream_options"
    assert not STRIPPED_TELEMETRY_FIELDS & OVERFLOW_VIEW_FIELDS
    # A ``stream_options`` that survives the projection declines the view as an unknown field.
    assert STREAM_OPTIONS_FIELD not in OVERFLOW_VIEW_FIELDS
    assert SERVICE_TIER_FIELD not in OVERFLOW_VIEW_FIELDS
    assert DECLINE_REASONS == frozenset(
        {
            "not_portable_history",
            "not_portable_lite_namespace",
            "not_portable_tools",
            "not_portable_items",
            "not_portable_vision",
            "not_portable_unknown_field",
            "turn_state_bound",
        }
    )


# --- fixtures: gpt-5.5 standard body and gpt-5.6 Responses-Lite bundle ---------------


@pytest.mark.parametrize("through_request_model", [False, True], ids=["raw", "model_dump_for_forwarding"])
def test_gpt55_standard_first_turn_strips_exactly_two_fields_and_builds_a_view(through_request_model: bool) -> None:
    fixture = _load_fixture("gpt55_standard_first_turn.json")
    body = _forwarded(fixture) if through_request_model else dict(fixture)
    before = copy.deepcopy(body)

    stripped = strip_source_telemetry(body)

    assert set(before) - set(stripped) == {"client_metadata", "stream_options"}
    assert stripped["prompt_cache_key"] == fixture["prompt_cache_key"]
    assert json.dumps(stripped["tools"], sort_keys=True) == json.dumps(fixture["tools"], sort_keys=True)
    assert stripped["include"] == ["reasoning.encrypted_content"]
    assert "service_tier" not in stripped  # never sent on a standard-tier turn; nothing synthesized

    view = overflow_portability_view(stripped)

    assert isinstance(view, PortabilityView)
    assert dict(view.body) == stripped


@pytest.mark.parametrize("through_request_model", [False, True], ids=["raw", "model_dump_for_forwarding"])
def test_gpt56_lite_bundle_declines_lite_namespace_while_the_stripped_body_still_forwards(
    through_request_model: bool,
) -> None:
    fixture = _load_fixture("gpt56_lite_bundle.json")
    body = _forwarded(fixture) if through_request_model else dict(fixture)

    stripped = strip_source_telemetry(body, strip_service_tier=True)

    # Direct routing keeps forwarding the Lite wire shape untouched (CP-7 / CP-1).
    assert "tools" not in stripped
    input_items = cast(list[dict[str, JsonValue]], stripped["input"])
    bundle = input_items[0]
    assert bundle["type"] == "additional_tools" and bundle["role"] == "developer"
    bundle_tools = cast(list[dict[str, JsonValue]], bundle["tools"])
    assert [tool["type"] for tool in bundle_tools] == ["namespace", "namespace", "namespace"]
    assert [tool["name"] for tool in bundle_tools] == ["functions", "web", "image_gen"]
    functions = cast(list[dict[str, JsonValue]], bundle_tools[0]["tools"])
    assert {function["name"] for function in functions} == {"exec", "wait"}
    developer = input_items[1]
    passthrough = developer["internal_chat_message_metadata_passthrough"]
    assert passthrough == {"content_item_kinds": ["model.base_instructions"]}
    assert stripped["reasoning"] == {"effort": "medium", "summary": "auto", "context": "all_turns"}
    assert "client_metadata" not in stripped and "stream_options" not in stripped

    # Overflow declines it as Lite -- never as history (mutants: hoist a Lite bundle; Lite declined as history).
    assert overflow_portability_view(stripped) == Declined("not_portable_lite_namespace", "reasoning.context")
    without_context = dict(stripped)
    without_context["reasoning"] = {"effort": "medium", "summary": "auto"}
    assert overflow_portability_view(without_context) == Declined("not_portable_lite_namespace", "additional_tools")
