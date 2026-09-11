from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import get_args

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_relocation import (
    RELOCATION_AMBIGUITY_WINDOW_SECONDS,
    ClientResendProof,
    RelocationDeclineReason,
    RelocationEvidence,
    RelocationInputs,
    RelocationSource,
    RelocationTransport,
    decide_relocation,
)

_TRANSPORTS: tuple[RelocationTransport, ...] = get_args(RelocationTransport)
_DECLINE_REASONS: frozenset[str] = frozenset(get_args(RelocationDeclineReason))


@dataclass(frozen=True)
class _Operation:
    request_text: str | None


@dataclass(frozen=True)
class _Turn:
    operation: _Operation
    events: tuple[str, ...]


def _frame(body: dict[str, JsonValue]) -> str:
    return json.dumps({"type": "response.create", **body}, separators=(",", ":"))


def _sse(payload: dict[str, JsonValue]) -> str:
    return f"event: {payload['type']}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"


def _user(text: str) -> dict[str, JsonValue]:
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _assistant(text: str) -> dict[str, JsonValue]:
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


def _completed_turn(prompt: str, answer: str, *, response_id: str) -> _Turn:
    return _Turn(
        operation=_Operation(request_text=_frame({"model": "gpt-5.4", "input": [_user(prompt)]})),
        events=(
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "output": [
                            {"id": f"rs_{response_id}", "type": "reasoning", "summary": []},
                            {"id": f"msg_{response_id}", **_assistant(answer)},
                        ],
                    },
                }
            ),
        ),
    )


_ANCHOR = "resp_owner"
_DELTA_PAYLOAD: dict[str, JsonValue] = {
    "model": "gpt-5.4",
    "input": [_user("and now?")],
    "previous_response_id": _ANCHOR,
}
_FULL_RESEND_PAYLOAD: dict[str, JsonValue] = {
    "model": "gpt-5.4",
    "input": [_user("hello"), _assistant("hi there"), _user("and now?")],
    "previous_response_id": _ANCHOR,
}
_UNANCHORED_PAYLOAD: dict[str, JsonValue] = {"model": "gpt-5.4", "input": [_user("hello")]}
_TRANSCRIPT = (_completed_turn("hello", "hi there", response_id="resp_1"),)
_RESEND_PROOF = ClientResendProof(stored_input_item_count=1)

_OWNER_METADATA: JsonValue = {"turn_id": "turn-owner"}
# The shape a Codex client actually resends: every item the owner account minted
# comes back carrying that account's item ids, and the turn's reasoning item
# comes with it. A body judged before projection carries all of it.
_PRODUCTION_FULL_RESEND_INPUT: list[JsonValue] = [
    {
        "role": "user",
        "content": [{"type": "input_text", "text": "old question"}],
        "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
    },
    {
        "type": "reasoning",
        "id": "rs_owner",
        "encrypted_content": "encrypted-owner-scoped-reasoning",
        "summary": [],
        "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
    },
    {
        "type": "function_call",
        "id": "fc_owner",
        "call_id": "call_old",
        "name": "lookup",
        "arguments": "{}",
        "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
    },
    {
        "type": "function_call_output",
        "call_id": "call_old",
        "output": "old output",
        "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
    },
    {
        "type": "message",
        "id": "msg_owner",
        "role": "assistant",
        "status": "completed",
        "phase": "final_answer",
        "content": [{"type": "output_text", "text": "old answer"}],
        "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
    },
    {
        "role": "user",
        "content": [{"type": "input_text", "text": "next question"}],
        "internal_chat_message_metadata_passthrough": {"turn_id": "turn-next"},
    },
]
_PRODUCTION_FULL_RESEND_PAYLOAD: dict[str, JsonValue] = {
    "model": "gpt-5.4",
    "instructions": "hi",
    "input": _PRODUCTION_FULL_RESEND_INPUT,
    "previous_response_id": _ANCHOR,
}
_PRODUCTION_RESEND_PROOF = ClientResendProof(stored_input_item_count=2)


def _unanchored(transport: RelocationTransport, **overrides: object) -> RelocationInputs:
    return replace(
        RelocationInputs(transport=transport, payload=_UNANCHORED_PAYLOAD, evidence="definitive"),
        **overrides,
    )


def _client_full_resend(transport: RelocationTransport, **overrides: object) -> RelocationInputs:
    return replace(
        RelocationInputs(
            transport=transport,
            payload=_FULL_RESEND_PAYLOAD,
            evidence="definitive",
            previous_response_id=_ANCHOR,
            client_resend=_RESEND_PROOF,
        ),
        **overrides,
    )


def _durable_transcript(transport: RelocationTransport, **overrides: object) -> RelocationInputs:
    return replace(
        RelocationInputs(
            transport=transport,
            payload=_DELTA_PAYLOAD,
            evidence="definitive",
            previous_response_id=_ANCHOR,
            durable_transcript=_TRANSCRIPT,
        ),
        **overrides,
    )


_SOURCE_BUILDERS = {
    "unanchored": _unanchored,
    "client_full_resend": _client_full_resend,
    "durable_transcript": _durable_transcript,
}
_AMBIGUITY_CLEARED: dict[str, object] = {
    "evidence": "ambiguous",
    "seconds_since_dispatch": 1.0,
    "arms_side_effect_replay_dedupe": True,
}


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
def test_definitive_evidence_relocates_on_every_source_and_transport(
    transport: RelocationTransport,
    source: RelocationSource,
) -> None:
    verdict = decide_relocation(_SOURCE_BUILDERS[source](transport))

    assert verdict.movable is True
    assert verdict.source == source
    assert verdict.decline_reason is None
    assert verdict.requires_recovery_fence is False
    assert verdict.body is not None
    assert "previous_response_id" not in verdict.body


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
def test_ambiguous_evidence_relocates_behind_the_recovery_fence(
    transport: RelocationTransport,
    source: RelocationSource,
) -> None:
    verdict = decide_relocation(_SOURCE_BUILDERS[source](transport, **_AMBIGUITY_CLEARED))

    assert verdict.movable is True
    assert verdict.source == source
    assert verdict.requires_recovery_fence is True


@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
@pytest.mark.parametrize("evidence", ["definitive", "ambiguous"])
def test_every_transport_reaches_the_same_verdict(source: RelocationSource, evidence: RelocationEvidence) -> None:
    overrides = dict(_AMBIGUITY_CLEARED) if evidence == "ambiguous" else {}
    verdicts = [decide_relocation(_SOURCE_BUILDERS[source](transport, **overrides)) for transport in _TRANSPORTS]

    assert len(_TRANSPORTS) == 3
    assert all(verdict == verdicts[0] for verdict in verdicts)


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_client_full_resend_outranks_the_durable_transcript(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _client_full_resend(transport, durable_transcript=_TRANSCRIPT),
    )

    assert verdict.source == "client_full_resend"
    assert verdict.body is not None
    assert verdict.body["input"] == _FULL_RESEND_PAYLOAD["input"]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_client_full_resend_relocates_on_the_projected_body(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=_PRODUCTION_FULL_RESEND_PAYLOAD,
            evidence="definitive",
            previous_response_id=_ANCHOR,
            client_resend=_PRODUCTION_RESEND_PROOF,
        ),
    )

    assert verdict.movable is True
    assert verdict.source == "client_full_resend"
    assert verdict.body is not None
    assert verdict.body["input"] == [
        _PRODUCTION_FULL_RESEND_INPUT[0],
        {
            "type": "function_call",
            "call_id": "call_old",
            "name": "lookup",
            "arguments": "{}",
            "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
        },
        _PRODUCTION_FULL_RESEND_INPUT[3],
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": "old answer"}],
            "internal_chat_message_metadata_passthrough": _OWNER_METADATA,
        },
        _PRODUCTION_FULL_RESEND_INPUT[5],
    ]
    assert verdict.body["instructions"] == "hi"
    assert "previous_response_id" not in verdict.body


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_durable_transcript_rebuilds_the_conversation_the_client_did_not_resend(
    transport: RelocationTransport,
) -> None:
    verdict = decide_relocation(_durable_transcript(transport))

    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_durable_transcript_accepts_a_verbatim_bridge_frame(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _durable_transcript(transport, current_request_text=_frame(_DELTA_PAYLOAD)),
    )

    assert verdict.movable is True
    assert verdict.source == "durable_transcript"


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
def test_visible_downstream_output_ends_eligibility_before_anything_else(
    transport: RelocationTransport,
    source: RelocationSource,
) -> None:
    verdict = decide_relocation(
        _SOURCE_BUILDERS[source](
            transport,
            downstream_output_visible=True,
            routing_strategy="single_account",
            input_file_pinned=True,
        ),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "downstream_output_visible"
    assert verdict.body is None
    assert verdict.source is None
    assert verdict.requires_recovery_fence is False


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
@pytest.mark.parametrize(
    ("ownership_fact", "expected_reason"),
    [
        ({"routing_strategy": "single_account"}, "single_account_routing"),
        ({"input_file_pinned": True}, "input_file_pinned"),
        ({"turn_state_owned": True}, "turn_state_owned"),
        ({"session_identity_bound": True}, "session_identity_bound"),
    ],
)
def test_ownership_facts_decline_every_source_on_every_transport(
    transport: RelocationTransport,
    source: RelocationSource,
    ownership_fact: dict[str, object],
    expected_reason: RelocationDeclineReason,
) -> None:
    verdict = decide_relocation(_SOURCE_BUILDERS[source](transport, **ownership_fact))

    assert verdict.movable is False
    assert verdict.decline_reason == expected_reason
    assert verdict.body is None


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_routing_strategies_other_than_single_account_do_not_bind(transport: RelocationTransport) -> None:
    verdict = decide_relocation(_unanchored(transport, routing_strategy="usage_weighted"))

    assert verdict.movable is True


def test_concurrent_ownership_facts_report_the_most_binding_one() -> None:
    verdict = decide_relocation(
        _unanchored(
            "http_bridge",
            routing_strategy="single_account",
            input_file_pinned=True,
            turn_state_owned=True,
            session_identity_bound=True,
        ),
    )

    assert verdict.decline_reason == "single_account_routing"


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
def test_absent_evidence_declines_before_any_body_is_built(
    transport: RelocationTransport,
    source: RelocationSource,
) -> None:
    verdict = decide_relocation(_SOURCE_BUILDERS[source](transport, evidence="none"))

    assert verdict.movable is False
    assert verdict.decline_reason == "no_relocation_evidence"
    assert verdict.body is None


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("evidence", ["definitive", "ambiguous"])
def test_an_anchored_turn_without_any_source_stays_owner_bound(
    transport: RelocationTransport,
    evidence: RelocationEvidence,
) -> None:
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=_DELTA_PAYLOAD,
            evidence=evidence,
            previous_response_id=_ANCHOR,
            seconds_since_dispatch=1.0,
            arms_side_effect_replay_dedupe=True,
        ),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "absent_transcript"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_full_resend_whose_suffix_is_unproven_is_not_a_source(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _client_full_resend(
            transport,
            # The stored turn accounted for the whole body, so nothing in it
            # continues that turn.
            client_resend=ClientResendProof(stored_input_item_count=3),
            durable_transcript=(_Turn(operation=_Operation(None), events=()),),
        ),
    )

    assert verdict.movable is False
    assert verdict.source is None
    assert verdict.decline_reason == "no_account_neutral_body"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_pending_tool_call_manifest_proves_a_tool_settling_suffix(transport: RelocationTransport) -> None:
    payload: dict[str, JsonValue] = {
        "model": "gpt-5.4",
        "input": [
            _user("hello"),
            {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        ],
        "previous_response_id": _ANCHOR,
    }
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=payload,
            evidence="definitive",
            previous_response_id=_ANCHOR,
            client_resend=ClientResendProof(
                stored_input_item_count=1,
                pending_tool_calls={"call_1": "function_call"},
            ),
        ),
    )

    assert verdict.movable is True
    assert verdict.source == "client_full_resend"


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "unrelocatable_state",
    [
        {"tools": [{"type": "code_interpreter"}]},
        {"input": [_user("hi"), {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": "{}"}]},
        {"input": [{"type": "input_file", "file_id": "file_owner"}]},
        {"conversation": "conv_owner"},
    ],
)
def test_a_body_the_strict_predicate_declines_is_not_a_source(
    transport: RelocationTransport,
    unrelocatable_state: dict[str, JsonValue],
) -> None:
    verdict = decide_relocation(
        _unanchored(transport, payload={**_UNANCHORED_PAYLOAD, **unrelocatable_state}),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "no_account_neutral_body"


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "execution_evidence",
    [{"spooled_event_count": 1}, {"response_id": "resp_replacement"}],
)
def test_proof_that_upstream_ran_the_turn_blocks_an_ambiguous_relocation(
    transport: RelocationTransport,
    execution_evidence: dict[str, object],
) -> None:
    verdict = decide_relocation(_unanchored(transport, **_AMBIGUITY_CLEARED, **execution_evidence))

    assert verdict.movable is False
    assert verdict.decline_reason == "upstream_execution_observed"
    assert verdict.requires_recovery_fence is False


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("blank_response_id", [None, "", "  "])
def test_a_blank_response_id_is_not_proof_that_upstream_ran_the_turn(
    transport: RelocationTransport,
    blank_response_id: str | None,
) -> None:
    verdict = decide_relocation(_unanchored(transport, **_AMBIGUITY_CLEARED, response_id=blank_response_id))

    assert verdict.movable is True
    assert verdict.requires_recovery_fence is True


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "seconds_since_dispatch",
    [None, RELOCATION_AMBIGUITY_WINDOW_SECONDS + 0.01, 600.0, -1.0],
)
def test_an_ambiguity_outside_the_window_is_not_relocated(
    transport: RelocationTransport,
    seconds_since_dispatch: float | None,
) -> None:
    verdict = decide_relocation(
        _unanchored(transport, evidence="ambiguous", seconds_since_dispatch=seconds_since_dispatch),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "outside_ambiguity_window"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_the_ambiguity_window_boundary_still_relocates(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _unanchored(
            transport,
            evidence="ambiguous",
            seconds_since_dispatch=RELOCATION_AMBIGUITY_WINDOW_SECONDS,
            arms_side_effect_replay_dedupe=True,
        ),
    )

    assert verdict.movable is True
    assert verdict.requires_recovery_fence is True


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "execution_evidence",
    [{"spooled_event_count": 1}, {"response_id": "resp_1"}],
)
def test_definitive_evidence_still_requires_that_upstream_emitted_nothing(
    transport: RelocationTransport,
    execution_evidence: dict[str, object],
) -> None:
    verdict = decide_relocation(_unanchored(transport, **execution_evidence))

    assert verdict.movable is False
    assert verdict.decline_reason == "upstream_execution_observed"
    assert verdict.body is None


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_definitive_evidence_needs_neither_the_window_nor_the_dedupe_contract(
    transport: RelocationTransport,
) -> None:
    verdict = decide_relocation(
        _unanchored(
            transport,
            seconds_since_dispatch=None,
            arms_side_effect_replay_dedupe=False,
        ),
    )

    assert verdict.movable is True
    assert verdict.requires_recovery_fence is False


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("source", sorted(_SOURCE_BUILDERS))
def test_a_transport_without_the_dedupe_contract_never_takes_the_fenced_lane(
    transport: RelocationTransport,
    source: RelocationSource,
) -> None:
    verdict = decide_relocation(
        _SOURCE_BUILDERS[source](
            transport,
            **{**_AMBIGUITY_CLEARED, "arms_side_effect_replay_dedupe": False},
        ),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "no_side_effect_replay_dedupe"
    assert verdict.requires_recovery_fence is False
    assert verdict.body is None


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("evidence", ["definitive", "ambiguous"])
def test_a_transport_without_durable_material_reports_an_absent_transcript(
    transport: RelocationTransport,
    evidence: RelocationEvidence,
) -> None:
    overrides = dict(_AMBIGUITY_CLEARED) if evidence == "ambiguous" else {}
    verdict = decide_relocation(_durable_transcript(transport, durable_transcript=None, **overrides))

    assert verdict.movable is False
    assert verdict.decline_reason == "absent_transcript"
    assert verdict.body is None
    assert verdict.requires_recovery_fence is False


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_rebuild_that_fails_is_not_reported_as_an_absent_transcript(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _durable_transcript(transport, durable_transcript=(_Turn(operation=_Operation(None), events=()),)),
    )

    assert verdict.decline_reason == "no_account_neutral_body"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_an_anchor_never_leaves_through_the_unanchored_branch(transport: RelocationTransport) -> None:
    anchor_free_body: dict[str, JsonValue] = {"model": "gpt-5.4", "input": [_user("and now?")]}
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=anchor_free_body,
            evidence="definitive",
            previous_response_id=_ANCHOR,
        ),
    )

    assert verdict.movable is False
    assert verdict.source is None
    assert verdict.decline_reason == "absent_transcript"


@pytest.mark.parametrize("blank_anchor", [None, "", "   "])
def test_a_blank_anchor_is_unanchored(blank_anchor: str | None) -> None:
    verdict = decide_relocation(_unanchored("http_bridge", previous_response_id=blank_anchor))

    assert verdict.source == "unanchored"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_an_anchor_the_caller_did_not_mirror_is_still_an_anchor(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=_DELTA_PAYLOAD,
            evidence="definitive",
            # The caller left the dedicated field unset; the body still names the
            # prior response, and stripping it would discard the conversation.
            previous_response_id=None,
        ),
    )

    assert verdict.movable is False
    assert verdict.source is None
    assert verdict.decline_reason == "absent_transcript"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_an_unmirrored_anchor_still_reaches_the_durable_rebuild(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=_DELTA_PAYLOAD,
            evidence="definitive",
            previous_response_id=None,
            durable_transcript=_TRANSCRIPT,
        ),
    )

    assert verdict.source == "durable_transcript"
    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_an_unreadable_durable_transcript_leaves_the_turn_owner_bound(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _durable_transcript(transport, durable_transcript=(_Turn(operation=_Operation(None), events=()),)),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "no_account_neutral_body"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_no_transcript_is_not_an_error(transport: RelocationTransport) -> None:
    for empty in (None, ()):
        verdict = decide_relocation(_durable_transcript(transport, durable_transcript=empty))

        assert verdict.movable is False
        assert verdict.decline_reason == "absent_transcript"


def test_every_decline_reason_is_in_the_closed_vocabulary() -> None:
    reasons = {
        decide_relocation(inputs).decline_reason
        for inputs in (
            _unanchored("http_bridge", downstream_output_visible=True),
            _unanchored("http_bridge", routing_strategy="single_account"),
            _unanchored("http_bridge", input_file_pinned=True),
            _unanchored("http_bridge", turn_state_owned=True),
            _unanchored("http_bridge", session_identity_bound=True),
            _unanchored("http_bridge", evidence="none"),
            _durable_transcript("http_bridge", durable_transcript=None),
            _durable_transcript("http_bridge", durable_transcript=(_Turn(operation=_Operation(None), events=()),)),
            _unanchored("http_bridge", **_AMBIGUITY_CLEARED, spooled_event_count=1),
            _unanchored("http_bridge", evidence="ambiguous", arms_side_effect_replay_dedupe=True),
            _unanchored("http_bridge", evidence="ambiguous", seconds_since_dispatch=1.0),
        )
    }

    assert reasons == _DECLINE_REASONS


def test_a_relocatable_verdict_never_carries_a_decline_reason() -> None:
    verdict = decide_relocation(_unanchored("websocket"))

    assert (verdict.movable, verdict.decline_reason) == (True, None)
    assert set(get_args(RelocationSource)) == set(_SOURCE_BUILDERS)
