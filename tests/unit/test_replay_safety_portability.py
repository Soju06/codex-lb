"""Provider-portability verdict on the overflow view (#2123 WP-C1, design v3 §4.4).

Covers the closed decline-reason table, the evaluation order (configuration-class
reasons before ``not_portable_history`` so the history hint is never offered for
a body a new conversation reproduces), the account-neutrality invariant
(portable => account-neutral fresh replay), ``transcript_is_source_free`` (steps
1-2), the binding turn-state header, and the two Codex-shaped fixtures end to
end through ``strip_source_telemetry`` and ``overflow_portability_view``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.openai.model_registry import MODEL_SOURCE_KIND_OPENAI_COMPATIBLE
from app.core.openai.requests import ResponsesRequest
from app.core.types import JsonValue
from app.db.models import ModelSource, ModelSourceModel
from app.modules.model_sources.catalog import source_model_supported_tool_types, source_model_supports_vision
from app.modules.model_sources.projection import (
    DECLINE_REASONS,
    OVERFLOW_VIEW_FIELDS,
    Declined,
    PortabilityView,
    overflow_portability_view,
    strip_source_telemetry,
)
from app.modules.proxy.replay_safety import (
    _ACCOUNT_NEUTRAL_TOOL_TYPES,
    _PORTABILITY_VIEW_ONLY_FIELDS,
    _RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION,
    _STATELESS_DECLARABLE_TOOL_TYPES,
    _STATELESS_TOOL_DECLARATION_FIELDS,
    PortabilityVerdict,
    _classification_view,
    input_carries_image_parts,
    is_binding_turn_state,
    responses_payload_is_account_neutral_fresh_replay,
    responses_payload_is_provider_portable,
    transcript_is_source_free,
)
from tests.unit.hypothesis_strategies import json_objects, json_values

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "codex_bodies"
NO_HEADERS: Mapping[str, str] = {}
BINDING_TURN_STATE: Mapping[str, str] = {"x-codex-turn-state": "client-turn-7f3a"}
SYNTHESIZED_TURN_STATE: Mapping[str, str] = {"x-codex-turn-state": "turn_" + "0123456789abcdef" * 2}

_DATA_IMAGE_URL = "data:image/png;base64,iVBORw0KGgo="


def _user(text: str) -> dict[str, JsonValue]:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant(text: str) -> dict[str, JsonValue]:
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def _function_tool(name: str = "shell") -> dict[str, JsonValue]:
    return {"type": "function", "name": name, "parameters": {"type": "object", "properties": {}}}


def _custom_tool(name: str = "apply_patch") -> dict[str, JsonValue]:
    return {"type": "custom", "name": name, "format": {"type": "text"}}


def _portable_body(**overrides: JsonValue) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "model": "gpt-5.5",
        "instructions": "You are Codex.",
        "input": [_user("hello")],
        "tools": [_function_tool()],
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
    }
    body.update(overrides)
    return body


def _view(body: Mapping[str, JsonValue]) -> PortabilityView:
    view = overflow_portability_view(body)
    assert isinstance(view, PortabilityView), view
    return view


def _verdict(
    body: Mapping[str, JsonValue],
    *,
    headers: Mapping[str, str] = NO_HEADERS,
    supported_tool_types: frozenset[str] = frozenset(),
    supports_vision: bool = False,
) -> PortabilityVerdict:
    return responses_payload_is_provider_portable(
        _view(body),
        headers,
        supported_tool_types=supported_tool_types,
        supports_vision=supports_vision,
    )


def _neutral(view: PortabilityView, supported_tool_types: frozenset[str] = frozenset()) -> bool:
    classification = _classification_view(view, supported_tool_types=supported_tool_types)
    return classification is not None and responses_payload_is_account_neutral_fresh_replay(classification)


def _source(
    *, model: str = "gpt-5.5", supports_vision: bool = False, raw: dict[str, JsonValue] | None = None
) -> ModelSource:
    return ModelSource(
        id="src_overflow",
        name="Overflow",
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url="http://127.0.0.1:8000/v1",
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=True,
        models=[
            ModelSourceModel(
                model=model,
                is_enabled=True,
                supports_streaming=True,
                supports_tools=True,
                supports_vision=supports_vision,
                raw_metadata_json=json.dumps(raw) if raw is not None else None,
            ),
            ModelSourceModel(model="disabled-vision", is_enabled=False, supports_vision=True),
        ],
    )


# --- the portable baseline --------------------------------------------------------------


def test_standard_first_turn_is_portable_and_account_neutral() -> None:
    verdict = _verdict(_portable_body())

    assert verdict == PortabilityVerdict(True)
    assert transcript_is_source_free(_view(_portable_body()))
    assert _neutral(_view(_portable_body()))


def test_declaring_the_tool_type_restores_portability() -> None:
    body = _portable_body(tools=[_function_tool(), _custom_tool(), {"type": "web_search"}])

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_tools", "custom")
    assert _verdict(body, supported_tool_types=frozenset({"custom"})) == PortabilityVerdict(
        False, "not_portable_tools", "web_search"
    )
    assert _verdict(body, supported_tool_types=frozenset({"custom", "web_search"})) == PortabilityVerdict(True)


def test_declaring_vision_restores_portability() -> None:
    body = _portable_body(
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "what is this"},
                    {"type": "input_image", "image_url": _DATA_IMAGE_URL, "detail": "auto"},
                ],
            }
        ]
    )

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_vision", "input_image")
    assert _verdict(body, supports_vision=True) == PortabilityVerdict(True)


def test_input_carries_image_parts_is_the_vision_step_alone() -> None:
    """The pinned dispatch's gate: message content and tool output parts count, a bare-string input never does."""

    image = {"type": "input_image", "image_url": _DATA_IMAGE_URL}
    assert input_carries_image_parts([{"type": "message", "role": "user", "content": [image]}])
    assert input_carries_image_parts([{"type": "function_call_output", "call_id": "c1", "output": [image]}])
    assert not input_carries_image_parts([{"type": "message", "role": "user", "content": [{"type": "input_text"}]}])
    assert not input_carries_image_parts([{"type": "message", "role": "user", "content": "plain"}])
    assert not input_carries_image_parts("plain text input")
    assert not input_carries_image_parts(None)


def test_catalog_declarations_drive_the_verdict() -> None:
    """``source_model_supported_tool_types`` and ``source_model_supports_vision`` feed the gate."""

    body = _portable_body(
        tools=[_function_tool(), _custom_tool()],
        input=[
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_image", "image_url": _DATA_IMAGE_URL}],
            }
        ],
    )
    bare = _source()
    declared = _source(supports_vision=True, raw={"experimental_supported_tools": ["custom"]})

    def verdict_for(source: ModelSource) -> PortabilityVerdict:
        return responses_payload_is_provider_portable(
            _view(body),
            NO_HEADERS,
            supported_tool_types=source_model_supported_tool_types(source, "gpt-5.5"),
            supports_vision=source_model_supports_vision(source, "gpt-5.5"),
        )

    assert verdict_for(bare) == PortabilityVerdict(False, "not_portable_tools", "custom")
    assert verdict_for(declared) == PortabilityVerdict(True)


def test_source_model_supports_vision_reads_the_enabled_entry_only() -> None:
    assert source_model_supports_vision(_source(supports_vision=True), "gpt-5.5") is True
    assert source_model_supports_vision(_source(supports_vision=False), "gpt-5.5") is False
    assert source_model_supports_vision(_source(supports_vision=True), "disabled-vision") is False
    assert source_model_supports_vision(_source(supports_vision=True), "unlisted") is False


# --- decline-reason table ---------------------------------------------------------------


def _reasoning_item() -> dict[str, JsonValue]:
    return {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAAA"}


_HISTORY_CASES: dict[str, dict[str, JsonValue]] = {
    "previous_response_id": _portable_body(previous_response_id="resp_prev"),
    "conversation": _portable_body(conversation="conv_1"),
    "prompt": _portable_body(prompt={"id": "pmpt_1"}),
    "retained item id": _portable_body(input=[{**_user("hello"), "id": "msg_1"}]),
    "reasoning item": _portable_body(input=[_user("q"), _reasoning_item(), _assistant("a"), _user("next")]),
    "compaction item": _portable_body(input=[{"type": "compaction", "encrypted_content": "gAAAA"}, _user("next")]),
    "item_reference": _portable_body(input=[{"type": "item_reference", "id": "msg_1"}, _user("next")]),
    "hosted computer_call": _portable_body(input=[{"type": "computer_call", "call_id": "c1", "action": {}}]),
    "mcp item": _portable_body(input=[{"type": "mcp_call", "id": "mcp_1", "name": "x", "server_label": "s"}]),
    "file id": _portable_body(
        input=[{"type": "message", "role": "user", "content": [{"type": "input_file", "file_id": "file_1"}]}]
    ),
    "account-scoped tool state": _portable_body(tools=[{"type": "function", "name": "f", "file_ids": ["file_1"]}]),
}


@pytest.mark.parametrize("case", sorted(_HISTORY_CASES))
def test_account_scoped_history_declines_as_history(case: str) -> None:
    body = _HISTORY_CASES[case]

    assert _verdict(body, supported_tool_types=frozenset({"custom", "web_search"}), supports_vision=True) == (
        PortabilityVerdict(False, "not_portable_history")
    )
    assert transcript_is_source_free(_view(body)) is False


_TOOL_CASES: dict[str, tuple[JsonValue, str]] = {
    "namespace even when declared": ([{"type": "namespace", "name": "functions", "tools": []}], "namespace"),
    "undeclared custom": ([_custom_tool()], "custom"),
    "undeclared web_search": ([{"type": "web_search"}], "web_search"),
    "undeclared hosted": ([{"type": "code_interpreter", "container": {"type": "auto"}}], "code_interpreter"),
    "non-object tool": (["shell"], "tools[]"),
    "untyped tool": ([{"name": "shell"}], "tools[].type"),
    "tools not a list": ({"type": "function"}, "tools"),
}


@pytest.mark.parametrize("case", sorted(_TOOL_CASES))
def test_undeclared_tool_types_decline_as_tools(case: str) -> None:
    tools, detail = _TOOL_CASES[case]

    verdict = _verdict(_portable_body(tools=tools), supported_tool_types=frozenset({"namespace"}))

    assert verdict == PortabilityVerdict(False, "not_portable_tools", detail)


def _call_pair(call_type: str, output_type: str | None, **call_fields: JsonValue) -> list[JsonValue]:
    items: list[JsonValue] = [{"type": call_type, "call_id": "call_1", **call_fields}]
    if output_type is not None:
        items.append({"type": output_type, "call_id": "call_1", "output": "ok"})
    return items


_ITEM_CASES: dict[str, tuple[list[JsonValue], str]] = {
    "custom_tool_call undeclared": (
        _call_pair("custom_tool_call", "custom_tool_call_output", name="shell", input="pwd"),
        "custom_tool_call",
    ),
    "apply_patch_call undeclared": (
        _call_pair("apply_patch_call", "apply_patch_call_output", operation={"type": "delete_file", "path": "a"}),
        "apply_patch_call",
    ),
    "shell_call undeclared": (_call_pair("shell_call", "shell_call_output"), "shell_call"),
    "local_shell_call undeclared": (_call_pair("local_shell_call", None), "local_shell_call"),
    "tool_search_call undeclared": (_call_pair("tool_search_call", "tool_search_output"), "tool_search_call"),
    "web_search_call undeclared": (
        [{"type": "web_search_call", "id": "ws_1", "status": "completed"}],
        "web_search_call",
    ),
    "bare content part": ([{"type": "input_text", "text": "hello"}], "input_text"),
    "bare input_image": ([{"type": "input_image", "image_url": _DATA_IMAGE_URL}], "input_image"),
    "unknown item type": ([{"type": "future_item", "value": 1}], "future_item"),
    "non-object item": (["hello"], "input[]"),
    "non-string item type": ([{"type": 7}], "input[].type"),
}


@pytest.mark.parametrize("case", sorted(_ITEM_CASES))
def test_unsupported_input_items_decline_as_items(case: str) -> None:
    items, detail = _ITEM_CASES[case]

    verdict = _verdict(_portable_body(input=[*items, _user("next")]), supports_vision=True)

    assert verdict == PortabilityVerdict(False, "not_portable_items", detail)


def test_declared_custom_tool_items_are_portable() -> None:
    body = _portable_body(
        tools=[_custom_tool("shell")],
        input=[
            _user("run pwd"),
            *_call_pair("custom_tool_call", "custom_tool_call_output", name="shell", input="pwd"),
            _assistant("done"),
            _user("thanks"),
        ],
    )

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_tools", "custom")
    assert _verdict(body, supported_tool_types=frozenset({"custom"})) == PortabilityVerdict(True)


def test_declared_hosted_call_items_still_fail_account_neutrality() -> None:
    """Retained ``web_search_call`` output is response-owned: declaring the tool names the reason, not a path."""

    body = _portable_body(
        tools=[{"type": "web_search"}],
        input=[_user("search"), {"type": "web_search_call", "status": "completed"}, _assistant("found"), _user("ok")],
    )

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_tools", "web_search")
    assert _verdict(body, supported_tool_types=frozenset({"web_search"})) == PortabilityVerdict(
        False, "not_portable_history"
    )


def test_image_parts_inside_tool_output_require_vision() -> None:
    body = _portable_body(
        input=[
            _user("view"),
            {"type": "function_call", "call_id": "call_1", "name": "view_image", "arguments": "{}"},
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": [{"type": "input_image", "image_url": _DATA_IMAGE_URL}],
            },
            _assistant("looks fine"),
            _user("ok"),
        ]
    )

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_vision", "input_image")
    assert _verdict(body, supports_vision=True) == PortabilityVerdict(True)


def test_flat_lite_bundle_in_a_hand_built_view_declines_as_lite_never_history() -> None:
    """Step 3 stays explicit: a synthetic flat bundle survives the account-neutral predicate."""

    bundle: dict[str, JsonValue] = {
        "type": "additional_tools",
        "role": "developer",
        "tools": [{"type": "custom", "name": "shell"}],
    }
    view = PortabilityView(body={**_portable_body(), "input": [bundle, _user("hello")]})

    assert _neutral(view) is True
    assert responses_payload_is_provider_portable(
        view, NO_HEADERS, supported_tool_types=frozenset({"custom"}), supports_vision=True
    ) == PortabilityVerdict(False, "not_portable_lite_namespace", "additional_tools")


def test_hand_built_view_with_an_unknown_field_fails_closed_as_history() -> None:
    view = PortabilityView(body={**_portable_body(), "x_future_field": 1})

    assert responses_payload_is_provider_portable(
        view, NO_HEADERS, supported_tool_types=frozenset(), supports_vision=False
    ) == PortabilityVerdict(False, "not_portable_history")


# --- turn state (step 7) ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("headers", "binding"),
    [
        ({}, False),
        ({"x-codex-turn-state": ""}, False),
        ({"x-codex-turn-state": "   "}, False),
        (SYNTHESIZED_TURN_STATE, False),
        ({"x-codex-turn-state": "http_turn_" + "f" * 32}, False),
        (BINDING_TURN_STATE, True),
        ({"X-Codex-Turn-State": "client-turn"}, True),
        ({"x-codex-turn-state": "turn_" + "f" * 31}, True),
        ({"x-codex-turn-state": "turn_" + "F" * 32}, True),
    ],
)
def test_is_binding_turn_state(headers: Mapping[str, str], binding: bool) -> None:
    assert is_binding_turn_state(headers) is binding


def test_binding_turn_state_is_the_last_reason() -> None:
    assert _verdict(_portable_body(), headers=BINDING_TURN_STATE) == PortabilityVerdict(False, "turn_state_bound")
    assert _verdict(_portable_body(), headers=SYNTHESIZED_TURN_STATE) == PortabilityVerdict(True)
    # Any other reason wins over the turn state.
    assert _verdict(_portable_body(previous_response_id="resp_1"), headers=BINDING_TURN_STATE) == PortabilityVerdict(
        False, "not_portable_history"
    )


# --- evaluation order ---------------------------------------------------------------------


def test_evaluation_order_reports_configuration_class_reasons_before_history() -> None:
    """A body failing every step declines in the documented order as each fault is removed."""

    image_message: dict[str, JsonValue] = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_image", "image_url": _DATA_IMAGE_URL}],
    }
    faults: dict[str, JsonValue] = {
        "input": [
            {"type": "additional_tools", "role": "developer", "tools": [{"type": "custom", "name": "shell"}]},
            *_call_pair("custom_tool_call", "custom_tool_call_output", name="shell", input="pwd"),
            image_message,
            _user("next"),
        ],
        "tools": [_function_tool(), {"type": "namespace", "name": "functions", "tools": []}],
        "previous_response_id": "resp_prev",
    }
    headers = BINDING_TURN_STATE
    body = _portable_body(**faults)
    view = PortabilityView(body=body)

    def verdict(current: PortabilityView, **kwargs: object) -> PortabilityVerdict:
        return responses_payload_is_provider_portable(
            current,
            headers,
            supported_tool_types=cast(frozenset[str], kwargs.get("supported_tool_types", frozenset())),
            supports_vision=cast(bool, kwargs.get("supports_vision", False)),
        )

    assert verdict(view).reason == "not_portable_lite_namespace"  # step 3
    input_items = cast(list[JsonValue], body["input"])[1:]
    view = PortabilityView(body={**body, "input": input_items})
    assert verdict(view) == PortabilityVerdict(False, "not_portable_tools", "namespace")  # step 5
    view = PortabilityView(body={**view.body, "tools": [_function_tool()]})
    assert verdict(view) == PortabilityVerdict(False, "not_portable_items", "custom_tool_call")  # step 4
    assert verdict(view, supported_tool_types=frozenset({"custom"})) == PortabilityVerdict(
        False, "not_portable_vision", "input_image"
    )  # step 6
    assert verdict(view, supported_tool_types=frozenset({"custom"}), supports_vision=True) == PortabilityVerdict(
        False, "not_portable_history"
    )  # steps 1-2
    view = PortabilityView(body={key: value for key, value in view.body.items() if key != "previous_response_id"})
    assert verdict(view, supported_tool_types=frozenset({"custom"}), supports_vision=True) == PortabilityVerdict(
        False, "turn_state_bound"
    )  # step 7
    assert responses_payload_is_provider_portable(
        view, NO_HEADERS, supported_tool_types=frozenset({"custom"}), supports_vision=True
    ) == PortabilityVerdict(True)


# --- transcript_is_source_free (steps 1-2) ------------------------------------------------


def test_transcript_is_source_free_agrees_with_steps_one_and_two() -> None:
    assert transcript_is_source_free(_view(_portable_body())) is True
    # Configuration-class faults do not make a transcript source-bound...
    assert transcript_is_source_free(_view(_portable_body(tools=[_custom_tool()]))) is True
    # ...except where the account-neutral predicate itself rejects the vocabulary.
    assert transcript_is_source_free(_view(_portable_body(tools=[{"type": "namespace", "name": "n"}]))) is False
    assert transcript_is_source_free(_view(_portable_body(previous_response_id="resp_1"))) is False
    assert transcript_is_source_free(_view(_portable_body(input=[_user("q"), _reasoning_item()]))) is False
    view_with_compaction = PortabilityView(body={**_portable_body(), "input": [{"type": "compaction"}, _user("q")]})
    assert transcript_is_source_free(view_with_compaction) is False


# --- properties ---------------------------------------------------------------------------


_texts = st.text(min_size=1, max_size=12).filter(lambda text: text.strip() != "")
_roles = st.sampled_from(["user", "developer", "assistant"])


@st.composite
def _message_items(draw: st.DrawFn) -> dict[str, JsonValue]:
    role = draw(_roles)
    part_type = "output_text" if role == "assistant" else draw(st.sampled_from(["input_text", "input_image"]))
    part: dict[str, JsonValue] = (
        {"type": "input_image", "image_url": _DATA_IMAGE_URL}
        if part_type == "input_image"
        else {"type": part_type, "text": draw(_texts)}
    )
    item: dict[str, JsonValue] = {"type": "message", "role": role, "content": [part]}
    if draw(st.booleans()):
        item.pop("type")
    if draw(st.integers(min_value=0, max_value=9)) == 0:
        item["id"] = "msg_" + draw(_texts)
    return item


_special_items: st.SearchStrategy[JsonValue] = st.sampled_from(
    cast(
        list[JsonValue],
        [
            {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAAA"},
            {"type": "compaction", "encrypted_content": "gAAAA"},
            {"type": "item_reference", "id": "msg_1"},
            {"type": "additional_tools", "role": "developer", "tools": [{"type": "custom", "name": "shell"}]},
            {
                "type": "additional_tools",
                "role": "developer",
                "tools": [{"type": "namespace", "name": "functions", "tools": []}],
            },
            {"type": "custom_tool_call", "call_id": "call_p", "name": "shell", "input": "pwd"},
            {"type": "web_search_call", "status": "completed"},
            {"type": "input_text", "text": "bare"},
            {"type": "future_item"},
        ],
    )
)
_tools: st.SearchStrategy[JsonValue] = st.lists(
    st.sampled_from(
        cast(
            list[JsonValue],
            [
                _function_tool(),
                _custom_tool(),
                {"type": "web_search"},
                {"type": "namespace", "name": "functions", "tools": []},
                {"type": "function", "name": "f", "file_ids": ["file_1"]},
                {"type": "apply_patch"},
                {"type": "shell", "file_ids": ["file_1"]},
                {"type": "code_interpreter", "container": "cntr_previous_account"},
                {"type": "apply_patch", "container": "cntr_previous_account"},
                {"type": "apply_patch", "description": "Edit files."},
            ],
        )
    ),
    max_size=3,
)
_reasoning = st.one_of(
    st.none(),
    st.fixed_dictionaries({}, optional={"effort": st.sampled_from(["low", "high"]), "summary": st.just("auto")}),
    st.just({"effort": "high", "context": "all_turns"}),
)
_extra_fields = st.dictionaries(
    st.sampled_from(["previous_response_id", "conversation", "x_future_field", "client_metadata", "service_tier"]),
    st.just("value"),
    max_size=2,
)


@st.composite
def _bodies(draw: st.DrawFn) -> dict[str, JsonValue]:
    items: list[JsonValue] = list(draw(st.lists(st.one_of(_message_items(), _special_items), max_size=5)))
    body: dict[str, JsonValue] = {"model": "gpt-5.5", "instructions": "x", "input": items, "store": False}
    if draw(st.booleans()):
        body["tools"] = draw(_tools)
    reasoning = draw(_reasoning)
    if reasoning is not None:
        body["reasoning"] = reasoning
    body.update(draw(_extra_fields))
    return body


_supported = st.frozensets(
    st.sampled_from(["custom", "web_search", "apply_patch", "shell", "namespace", "code_interpreter"]), max_size=3
)


@settings(max_examples=250, deadline=None)
@given(_bodies(), _supported, st.booleans(), st.sampled_from([NO_HEADERS, BINDING_TURN_STATE, SYNTHESIZED_TURN_STATE]))
def test_portable_implies_account_neutral_fresh_replay(
    body: dict[str, JsonValue],
    supported_tool_types: frozenset[str],
    supports_vision: bool,
    headers: Mapping[str, str],
) -> None:
    stripped = strip_source_telemetry(dict(body), strip_service_tier=True)
    view = overflow_portability_view(stripped)
    if isinstance(view, Declined):
        assert view.reason in {"not_portable_unknown_field", "not_portable_lite_namespace"}
        return

    verdict = responses_payload_is_provider_portable(
        view, headers, supported_tool_types=supported_tool_types, supports_vision=supports_vision
    )
    source_free = transcript_is_source_free(view, supported_tool_types=supported_tool_types)
    neutral = _neutral(view, supported_tool_types)
    # Without declarations the check is the predicate's own and never more permissive.
    assert not transcript_is_source_free(view) or source_free

    if verdict.portable:
        assert verdict.reason is None and verdict.detail is None
        assert neutral and source_free
        assert not is_binding_turn_state(headers)
        assert all(
            tool_type_ok(tool, supported_tool_types) for tool in cast(list[JsonValue], view.body.get("tools") or [])
        )
    else:
        assert verdict.reason in DECLINE_REASONS
        if verdict.reason == "not_portable_history":
            assert not source_free
        if verdict.reason == "turn_state_bound":
            assert source_free and is_binding_turn_state(headers)
    assert source_free == (
        neutral and not any(_item_type(item) in ("reasoning", "compaction") for item in _items(view))
    )


def tool_type_ok(tool: JsonValue, supported_tool_types: frozenset[str]) -> bool:
    if not isinstance(tool, dict):
        return False
    tool_type = tool.get("type")
    if tool_type == "function":
        return True
    if not isinstance(tool_type, str) or tool_type not in supported_tool_types:
        return False
    if tool_type in _STATELESS_DECLARABLE_TOOL_TYPES:
        description = tool.get("description")
        return set(tool) <= _STATELESS_TOOL_DECLARATION_FIELDS and (description is None or isinstance(description, str))
    return tool_type in _ACCOUNT_NEUTRAL_TOOL_TYPES


def _items(view: PortabilityView) -> list[JsonValue]:
    value = view.body.get("input")
    return cast(list[JsonValue], value) if isinstance(value, list) else []


def _item_type(item: JsonValue) -> str | None:
    if not isinstance(item, dict):
        return None
    value = item.get("type")
    return value if isinstance(value, str) else None


@settings(max_examples=150, deadline=None)
@given(json_objects, st.booleans())
def test_verdict_never_raises_on_arbitrary_views(body: dict[str, JsonValue], supports_vision: bool) -> None:
    verdict = responses_payload_is_provider_portable(
        PortabilityView(body=body), NO_HEADERS, supported_tool_types=frozenset(), supports_vision=supports_vision
    )

    assert isinstance(verdict, PortabilityVerdict)
    if not verdict.portable:
        assert verdict.reason in DECLINE_REASONS


# --- declared stateless tool declarations (codex review P2) ----------------------------------


@pytest.mark.parametrize("tool_type", ["apply_patch", "shell", "local_shell", "tool_search"])
def test_declaring_a_stateless_tool_type_the_predicate_does_not_know_restores_portability(tool_type: str) -> None:
    body = _portable_body(tools=[_function_tool(), {"type": tool_type}], tool_choice={"type": tool_type})

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_tools", tool_type)
    assert _verdict(body, supported_tool_types=frozenset({tool_type})) == PortabilityVerdict(True)
    # Without declarations the transcript check stays the predicate's own (conservative)...
    assert transcript_is_source_free(_view(body)) is False
    # ...and with them the declaration is set aside, not treated as history.
    assert transcript_is_source_free(_view(body), supported_tool_types=frozenset({tool_type})) is True


_HOSTED_DECLARATIONS: dict[str, dict[str, JsonValue]] = {
    "code_interpreter container": {"type": "code_interpreter", "container": "cntr_previous_account"},
    "file_search vector stores": {"type": "file_search", "vector_store_ids": ["vs_1"]},
    "mcp connector": {"type": "mcp", "server_label": "drive", "connector_id": "connector_googledrive"},
    "image_generation": {"type": "image_generation"},
    "computer_use_preview": {"type": "computer_use_preview", "display_width": 1, "display_height": 1},
}


@pytest.mark.parametrize("case", sorted(_HOSTED_DECLARATIONS))
def test_hosted_tool_declarations_are_never_portable_even_when_declared(case: str) -> None:
    """Hosted declarations carry provider/account-side state; declaring them serves direct routing only."""

    tool = _HOSTED_DECLARATIONS[case]
    tool_type = cast(str, tool["type"])
    body = _portable_body(tools=[_function_tool(), tool])

    assert _verdict(body) == PortabilityVerdict(False, "not_portable_tools", tool_type)
    assert _verdict(body, supported_tool_types=frozenset({tool_type})) == PortabilityVerdict(
        False, "not_portable_tools", tool_type
    )
    # The set-aside never touches a hosted declaration, so the predicate keeps rejecting it.
    classification = _classification_view(_view(body), supported_tool_types=frozenset({tool_type}))
    assert classification is not None and classification["tools"] == body["tools"]
    assert transcript_is_source_free(_view(body), supported_tool_types=frozenset({tool_type})) is False


_NON_STATELESS_SHAPES: dict[str, dict[str, JsonValue]] = {
    "account-bound container": {"type": "apply_patch", "container": "cntr_previous_account"},
    "container id": {"type": "shell", "container_id": "cntr_previous_account"},
    "file ids": {"type": "apply_patch", "file_ids": ["file_1"]},
    "unknown field": {"type": "tool_search", "execution": {"mode": "auto"}},
    "non-string description": {"type": "local_shell", "description": 5},
}


@pytest.mark.parametrize("case", sorted(_NON_STATELESS_SHAPES))
def test_stateless_declaration_outside_its_shape_is_never_set_aside(case: str) -> None:
    """Only ``type`` plus a string ``description`` is set aside; any other field declines as tools (review P1)."""

    tool = _NON_STATELESS_SHAPES[case]
    tool_type = cast(str, tool["type"])
    body = _portable_body(tools=[tool])
    declared = frozenset({tool_type})

    assert _verdict(body, supported_tool_types=declared) == PortabilityVerdict(False, "not_portable_tools", tool_type)
    assert transcript_is_source_free(_view(body), supported_tool_types=declared) is False
    classification = _classification_view(_view(body), supported_tool_types=declared)
    assert classification is not None and classification["tools"] == [tool]


def test_stateless_declaration_with_a_string_description_is_set_aside() -> None:
    body = _portable_body(
        tools=[{"type": "apply_patch", "description": "Edit files with patches."}],
        tool_choice={"type": "apply_patch"},
    )

    assert _verdict(body, supported_tool_types=frozenset({"apply_patch"})) == PortabilityVerdict(True)
    classification = _classification_view(_view(body), supported_tool_types=frozenset({"apply_patch"}))
    assert classification is not None and classification["tools"] == [] and "tool_choice" not in classification


def test_stateless_declarable_allowlist_is_closed_and_disjoint_from_the_predicate_vocabulary() -> None:
    assert _STATELESS_DECLARABLE_TOOL_TYPES == frozenset({"apply_patch", "local_shell", "shell", "tool_search"})
    assert _STATELESS_TOOL_DECLARATION_FIELDS == frozenset({"description", "type"})
    assert not _STATELESS_DECLARABLE_TOOL_TYPES & _ACCOUNT_NEUTRAL_TOOL_TYPES
    assert "namespace" not in _STATELESS_DECLARABLE_TOOL_TYPES | _ACCOUNT_NEUTRAL_TOOL_TYPES


def test_transcript_check_declines_malformed_item_types_without_raising() -> None:
    """``transcript_is_source_free`` is exposed on its own (neutral release) and must never raise."""

    malformed_inputs: tuple[list[JsonValue], ...] = (
        [{"type": [], "role": "user", "content": "hi"}],
        [{"type": {"nested": 1}, "role": "user", "content": "hi"}],
        ["hi"],
        [{"type": 7}],
    )
    for input_items in malformed_inputs:
        view = PortabilityView(body={**_portable_body(), "input": input_items})
        assert transcript_is_source_free(view) is False
        assert transcript_is_source_free(view, supported_tool_types=frozenset({"apply_patch"})) is False
        verdict = responses_payload_is_provider_portable(
            view, NO_HEADERS, supported_tool_types=frozenset(), supports_vision=True
        )
        assert verdict.portable is False and verdict.reason in DECLINE_REASONS


def test_predicate_known_declarations_keep_their_strict_field_allowlists_when_declared() -> None:
    body = _portable_body(tools=[{"type": "web_search", "unexpected": True}])

    assert _verdict(body, supported_tool_types=frozenset({"web_search"})) == PortabilityVerdict(
        False, "not_portable_history"
    )


# --- malformed nested values never raise (codex review P2) ------------------------------------


_MALFORMED_CASES: dict[str, dict[str, JsonValue]] = {
    "tool_choice type list": _portable_body(tool_choice={"type": []}),
    "tool_choice type object": _portable_body(tool_choice={"type": {"nested": 1}}),
    "allowed_tools mode list": _portable_body(
        tool_choice={"type": "allowed_tools", "mode": [], "tools": [{"type": "function", "name": "f"}]}
    ),
    "allowed_tools reference type list": _portable_body(
        tool_choice={"type": "allowed_tools", "mode": "auto", "tools": [{"type": []}]}
    ),
    "web_search search_context_size list": _portable_body(tools=[{"type": "web_search", "search_context_size": []}]),
    "custom grammar syntax list": _portable_body(
        tools=[{"type": "custom", "name": "c", "format": {"type": "grammar", "syntax": [], "definition": "x"}}]
    ),
    "text verbosity list": _portable_body(text={"verbosity": []}),
    "text format type list": _portable_body(text={"format": {"type": []}}),
    "message role list": _portable_body(input=[{"role": [], "content": "hi"}]),
    "message phase list": _portable_body(input=[{"role": "user", "content": "hi", "phase": []}]),
    "content part type list": _portable_body(input=[{"role": "user", "content": [{"type": [], "text": "hi"}]}]),
    "assistant part type list": _portable_body(input=[{"role": "assistant", "content": [{"type": [], "text": "x"}]}]),
}


@pytest.mark.parametrize("case", sorted(_MALFORMED_CASES))
def test_malformed_nested_values_decline_as_history_without_raising(case: str) -> None:
    body = _MALFORMED_CASES[case]

    assert responses_payload_is_account_neutral_fresh_replay(body) is False
    assert _verdict(body, supported_tool_types=frozenset({"custom", "web_search"}), supports_vision=True) == (
        PortabilityVerdict(False, "not_portable_history")
    )


def _neutral_baseline() -> dict[str, JsonValue]:
    """A body the raw replay predicate accepts, so every injected slot is really exercised."""

    return {
        "model": "gpt-5.5",
        "instructions": "You are Codex.",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hi"}]},
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "next"}]},
        ],
        "tools": [
            {"type": "web_search", "search_context_size": "low"},
            {"type": "custom", "name": "c", "format": {"type": "grammar", "syntax": "lark", "definition": "x"}},
        ],
        "tool_choice": {"type": "allowed_tools", "mode": "auto", "tools": [{"type": "web_search"}]},
        "text": {"verbosity": "low", "format": {"type": "text"}},
        "reasoning": {"effort": "low", "summary": "auto"},
        "include": ["reasoning.encrypted_content"],
        "store": False,
        "stream": True,
    }


_NESTED_SLOTS: tuple[tuple[str | int, ...], ...] = (
    ("tool_choice",),
    ("tool_choice", "type"),
    ("tool_choice", "mode"),
    ("tool_choice", "tools", 0, "type"),
    ("tools", 0, "type"),
    ("tools", 0, "search_context_size"),
    ("tools", 1, "format", "syntax"),
    ("tools", 1, "format", "type"),
    ("text", "verbosity"),
    ("text", "format", "type"),
    ("input", 0, "role"),
    ("input", 0, "phase"),
    ("input", 0, "status"),
    ("input", 0, "type"),
    ("input", 0, "content", 0, "type"),
    ("input", 1, "content", 0, "type"),
    ("reasoning", "effort"),
    ("include",),
    ("instructions",),
)


def _malformed_at(slot: tuple[str | int, ...], value: JsonValue) -> dict[str, JsonValue]:
    body = _neutral_baseline()
    cursor: JsonValue = body
    for key in slot[:-1]:
        cursor = (
            cast(Mapping[str, JsonValue], cursor)[key] if isinstance(key, str) else cast(list[JsonValue], cursor)[key]
        )
    last = slot[-1]
    if isinstance(last, str):
        cast(dict[str, JsonValue], cursor)[last] = value
    else:
        cast(list[JsonValue], cursor)[last] = value
    return body


def test_neutral_baseline_is_accepted_by_the_predicate_and_the_verdict() -> None:
    baseline = _neutral_baseline()

    assert responses_payload_is_account_neutral_fresh_replay(baseline) is True
    assert transcript_is_source_free(PortabilityView(body=baseline)) is True
    assert responses_payload_is_provider_portable(
        PortabilityView(body=baseline),
        NO_HEADERS,
        supported_tool_types=frozenset({"custom", "web_search"}),
        supports_vision=True,
    ) == PortabilityVerdict(True)


def test_list_typed_input_item_declines_without_raising_in_every_entry_point() -> None:
    body = _malformed_at(("input", 0, "type"), [])

    assert responses_payload_is_account_neutral_fresh_replay(body) is False
    assert transcript_is_source_free(PortabilityView(body=body)) is False
    verdict = responses_payload_is_provider_portable(
        PortabilityView(body=body),
        NO_HEADERS,
        supported_tool_types=frozenset({"custom", "web_search"}),
        supports_vision=True,
    )
    assert verdict == PortabilityVerdict(False, "not_portable_items", "input[].type")


@settings(max_examples=250, deadline=None)
@given(st.sampled_from(_NESTED_SLOTS), json_values)
def test_arbitrary_values_in_known_nested_slots_never_raise(slot: tuple[str | int, ...], value: JsonValue) -> None:
    body = _malformed_at(slot, value)

    assert isinstance(responses_payload_is_account_neutral_fresh_replay(body), bool)
    view = PortabilityView(body=body)
    assert isinstance(transcript_is_source_free(view), bool)
    verdict = responses_payload_is_provider_portable(
        view, NO_HEADERS, supported_tool_types=frozenset({"custom", "web_search"}), supports_vision=True
    )
    assert verdict.portable or verdict.reason in DECLINE_REASONS


# --- constants cannot drift -----------------------------------------------------------------


def test_view_allowlist_and_account_neutral_validation_set_are_pinned_against_each_other() -> None:
    assert OVERFLOW_VIEW_FIELDS - _RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION == _PORTABILITY_VIEW_ONLY_FIELDS
    assert _RESPONSES_PAYLOAD_FIELDS_WITH_DEDICATED_VALIDATION - OVERFLOW_VIEW_FIELDS == {
        "client_metadata",
        "service_tier",
    }
    assert _PORTABILITY_VIEW_ONLY_FIELDS == frozenset(
        {"max_output_tokens", "prompt_cache_retention", "safety_identifier", "temperature", "top_p", "user"}
    )


# --- Codex-shaped fixtures end to end ---------------------------------------------------------


def _fixture(name: str) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _forwarded(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return ResponsesRequest.model_validate(body).model_dump_for_forwarding()


def test_gpt55_standard_first_turn_is_portable_once_custom_is_declared() -> None:
    stripped = strip_source_telemetry(_forwarded(_fixture("gpt55_standard_first_turn.json")), strip_service_tier=True)
    view = _view(stripped)

    undeclared = responses_payload_is_provider_portable(
        view, NO_HEADERS, supported_tool_types=frozenset(), supports_vision=False
    )
    assert undeclared == PortabilityVerdict(False, "not_portable_tools", "custom")

    declared_source = _source(raw={"experimental_supported_tools": ["custom"]})
    portable = responses_payload_is_provider_portable(
        view,
        NO_HEADERS,
        supported_tool_types=source_model_supported_tool_types(declared_source, "gpt-5.5"),
        supports_vision=source_model_supports_vision(declared_source, "gpt-5.5"),
    )
    assert portable == PortabilityVerdict(True)
    assert transcript_is_source_free(view)


def test_gpt56_lite_bundle_is_declined_as_lite_by_the_view_and_by_the_verdict() -> None:
    stripped = strip_source_telemetry(_forwarded(_fixture("gpt56_lite_bundle.json")), strip_service_tier=True)

    assert overflow_portability_view(stripped) == Declined("not_portable_lite_namespace", "reasoning.context")

    # Even a view that bypassed the builder never reports the bundle as history.
    verdict = responses_payload_is_provider_portable(
        PortabilityView(body=stripped),
        NO_HEADERS,
        supported_tool_types=frozenset({"custom", "namespace"}),
        supports_vision=True,
    )
    assert verdict == PortabilityVerdict(False, "not_portable_lite_namespace", "additional_tools")
