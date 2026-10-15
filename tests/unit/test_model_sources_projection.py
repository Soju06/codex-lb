"""Source-body projection: Codex telemetry stripping for direct model-source routing.

The two bodies this module loads are the *pre-strip* fixtures of
``tests/fixtures/codex_bodies``: synthetic, and kept synthetic because they
carry the Codex telemetry this module's stripper has to remove. They are shaped
after Codex's request construction (``client.rs``/``responses_metadata.rs`` for
the gpt-5.5 standard body; ``core/tests/suite/responses_lite.rs`` for the
gpt-5.6 Responses-Lite bundle: ``namespace`` tools
``functions``->``exec``/``wait``, ``web``->``run``, ``image_gen``->``imagegen``,
the tagged developer base-instructions message, ``reasoning.context=all_turns``,
no top-level ``tools``) but they are shape-illustrative, not byte-faithful.

Real captured bodies live alongside them; the corpus and the per-fixture
divergences from Codex 0.154.0 are in
``tests/fixtures/codex_bodies/provenance.json`` and its README, gated by
``tests/unit/test_codex_body_fixtures.py``.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest

from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.modules.model_sources.projection import (
    STREAM_OPTIONS_FIELD,
    STRIPPED_STREAM_OPTIONS_KEYS,
    STRIPPED_TELEMETRY_FIELDS,
    strip_source_telemetry,
)

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


def test_strip_is_idempotent_and_tolerates_absent_fields() -> None:
    payload: dict[str, JsonValue] = {"model": "gpt-5.5", "input": "hello"}
    assert strip_source_telemetry(payload) == {"model": "gpt-5.5", "input": "hello"}

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


def test_a_surviving_stream_options_forwards_for_direct_routing() -> None:
    """An SDK client's ``stream_options.include_obfuscation`` is forwarded; only the Codex key goes."""

    sdk_shaped: dict[str, JsonValue] = {
        "model": "gpt-5.5",
        "input": [],
        "stream_options": {"include_obfuscation": True},
    }
    stripped = strip_source_telemetry(sdk_shaped)
    assert stripped["stream_options"] == {"include_obfuscation": True}


def test_strip_never_fails_closed_on_an_unknown_field() -> None:
    """CP-7: an unknown field is forwarded for direct routing, never dropped."""

    stripped = strip_source_telemetry({"model": "gpt-5.5", "input": [], "x_future_field": 1})
    assert stripped["x_future_field"] == 1


def test_constants_are_closed() -> None:
    assert STRIPPED_TELEMETRY_FIELDS == frozenset({"client_metadata", "access_programs"})
    assert STRIPPED_STREAM_OPTIONS_KEYS == frozenset({"reasoning_summary_delivery"})
    assert STREAM_OPTIONS_FIELD == "stream_options"


# --- fixtures: gpt-5.5 standard body and gpt-5.6 Responses-Lite bundle ---------------


@pytest.mark.parametrize("through_request_model", [False, True], ids=["raw", "model_dump_for_forwarding"])
def test_gpt55_standard_first_turn_strips_exactly_two_fields(through_request_model: bool) -> None:
    fixture = _load_fixture("gpt55_standard_first_turn.json")
    body = _forwarded(fixture) if through_request_model else dict(fixture)
    before = copy.deepcopy(body)

    stripped = strip_source_telemetry(body)

    assert set(before) - set(stripped) == {"client_metadata", "stream_options"}
    assert stripped["prompt_cache_key"] == fixture["prompt_cache_key"]
    assert json.dumps(stripped["tools"], sort_keys=True) == json.dumps(fixture["tools"], sort_keys=True)
    assert stripped["include"] == ["reasoning.encrypted_content"]
    assert "service_tier" not in stripped  # never sent on a standard-tier turn; nothing synthesized


@pytest.mark.parametrize("through_request_model", [False, True], ids=["raw", "model_dump_for_forwarding"])
def test_gpt56_lite_bundle_still_forwards_the_lite_wire_shape(through_request_model: bool) -> None:
    fixture = _load_fixture("gpt56_lite_bundle.json")
    body = _forwarded(fixture) if through_request_model else dict(fixture)

    stripped = strip_source_telemetry(body)

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
