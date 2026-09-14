from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any, get_args

import pytest

from app.core.types import JsonValue
from app.modules.proxy.replay_relocation import (
    RELOCATION_AMBIGUITY_WINDOW_SECONDS,
    RelocationDeclineReason,
    RelocationEvidence,
    RelocationInputs,
    RelocationSource,
    RelocationTransport,
    RelocationVerdict,
    decide_relocation,
)
from app.modules.proxy.replay_safety import (
    RELOCATION_TRANSCRIPT_MAX_BYTES,
    RELOCATION_TRANSCRIPT_MAX_ITEMS,
    RELOCATION_TRANSCRIPT_MAX_TURNS,
)

_TRANSPORTS: tuple[RelocationTransport, ...] = get_args(RelocationTransport)
_DECLINE_REASONS: frozenset[str] = frozenset(get_args(RelocationDeclineReason))


@dataclass(frozen=True)
class _Operation:
    request_text: str | None
    response_id: str | None = "resp_1"
    parent_response_id: str | None = None
    event_spool_complete: bool = True


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


def _spooled_assistant(text: str) -> dict[str, JsonValue]:
    # ``annotations`` rides on every ``output_text`` part the Responses API
    # produces, so the spool carries it and the rebuild has to deal with it.
    return {**_assistant(text), "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _completed_turn(
    prompt: str,
    answer: str,
    *,
    response_id: str,
    parent_response_id: str | None = None,
    stored_input: JsonValue | None = None,
) -> _Turn:
    return _Turn(
        operation=_Operation(
            request_text=_frame(
                {"model": "gpt-5.4", "input": [_user(prompt)] if stored_input is None else stored_input}
            ),
            response_id=response_id,
            parent_response_id=parent_response_id,
        ),
        events=(
            _sse(
                {
                    "type": "response.completed",
                    "response": {
                        "id": response_id,
                        "output": [
                            {"id": f"rs_{response_id}", "type": "reasoning", "summary": []},
                            {"id": f"msg_{response_id}", **_spooled_assistant(answer)},
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
_TRANSCRIPT = (_completed_turn("hello", "hi there", response_id=_ANCHOR),)

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


def _client_input(transport: RelocationTransport, **overrides: object) -> RelocationInputs:
    return replace(
        RelocationInputs(transport=transport, payload=_UNANCHORED_PAYLOAD, evidence="definitive"),
        **overrides,
    )


def _durable_transcript(transport: RelocationTransport, **overrides: object) -> RelocationInputs:
    return replace(
        RelocationInputs(
            transport=transport,
            payload=_DELTA_PAYLOAD,
            evidence="definitive",
            durable_transcript=_TRANSCRIPT,
        ),
        **overrides,
    )


_SOURCE_BUILDERS = {
    "client_input": _client_input,
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
def test_durable_transcript_rebuilds_the_conversation_the_client_did_not_resend(
    transport: RelocationTransport,
) -> None:
    verdict = decide_relocation(_durable_transcript(transport))

    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_an_anchored_full_resend_dispatches_the_body_the_client_sent(transport: RelocationTransport) -> None:
    # The client resent the whole thread and anchored it as well, which is a
    # shape this proxy already verifies. The overlap takes the entire chain, so
    # what is dispatched is the client's own request -- and the verdict must say
    # so, because there is not one rebuilt item in it.
    verdict = decide_relocation(_durable_transcript(transport, payload=_FULL_RESEND_PAYLOAD))

    assert verdict.movable is True
    assert verdict.source == "client_input"
    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_chain_turn_that_restated_the_conversation_replaces_what_it_restates(
    transport: RelocationTransport,
) -> None:
    # The thread's second recorded turn carried everything before it, which is
    # what a client sends the first time this proxy sees a conversation it did
    # not start. Concatenating it onto the history it restates doubles the
    # opening turn.
    chain = (
        _completed_turn("q1", "a1", response_id="resp_1"),
        _completed_turn(
            "unused",
            "a2",
            response_id=_ANCHOR,
            parent_response_id="resp_1",
            stored_input=[
                _user("q1"),
                {"id": "rs_resp_1", "type": "reasoning", "summary": []},
                {"id": "msg_resp_1", **_spooled_assistant("a1")},
                _user("q2"),
            ],
        ),
    )

    verdict = decide_relocation(_durable_transcript(transport, durable_transcript=chain))

    assert verdict.movable is True
    assert verdict.body is not None
    assert verdict.body["input"] == [
        _user("q1"),
        _assistant("a1"),
        _user("q2"),
        _assistant("a2"),
        _user("and now?"),
    ]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_an_anchored_tail_restatement_replaces_the_chains_copy_of_the_answer(
    transport: RelocationTransport,
) -> None:
    # The client restated the last answer and nothing before it. That answer is
    # the tail of the accumulation, so the chain's copy comes off and the
    # client's stands in its place, with the question it answered still in front
    # of it. The chain contributed that question, so the body is a rebuild.
    verdict = decide_relocation(
        _durable_transcript(
            transport,
            payload={**_DELTA_PAYLOAD, "input": [_assistant("hi there"), _user("and now?")]},
        ),
    )

    assert verdict.movable is True
    assert verdict.source == "durable_transcript"
    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_chain_turn_stored_as_a_scalar_is_that_turns_own_material(transport: RelocationTransport) -> None:
    # A stored scalar is the whole of that turn's prompt and restates nothing,
    # so it joins as the turn's delta. The client's opening message reads the
    # same as the chain's and is kept: only a content match could tell the
    # repeat from a coincidence, and deleting it would answer a conversation
    # the user never wrote.
    chain = (_completed_turn("hello", "hi there", response_id=_ANCHOR, stored_input="hello"),)

    verdict = decide_relocation(
        _durable_transcript(
            transport,
            payload={**_DELTA_PAYLOAD, "input": [_user("hello"), _user("and now?")]},
            durable_transcript=chain,
        ),
    )

    assert verdict.movable is True
    assert verdict.body is not None
    assert verdict.body["input"] == [
        _user("hello"),
        _assistant("hi there"),
        _user("hello"),
        _user("and now?"),
    ]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_the_bridge_frame_seam_joins_the_body_it_dispatches(transport: RelocationTransport) -> None:
    # The bridge decides on the exact frame it would have sent upstream, so the
    # resend inside that frame is the one the overlap is measured against.
    verdict = decide_relocation(
        _durable_transcript(
            transport,
            payload={"model": "gpt-5.4", "input": [_user("stale parse")]},
            current_request_text=_frame(_FULL_RESEND_PAYLOAD),
        ),
    )

    assert verdict.movable is True
    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_the_fenced_lane_spends_its_one_shot_on_the_conversation_once(transport: RelocationTransport) -> None:
    # The ambiguous lane spends a budget that cannot be refilled, so the body it
    # spends it on must not be the conversation twice over.
    verdict = decide_relocation(
        _durable_transcript(transport, payload=_FULL_RESEND_PAYLOAD, **_AMBIGUITY_CLEARED),
    )

    assert verdict.movable is True
    assert verdict.requires_recovery_fence is True
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
def test_the_verbatim_frame_decides_the_verdict_it_also_carries(transport: RelocationTransport) -> None:
    # The frame is what would have gone upstream; the parsed request beside it
    # is a second copy that need not agree. Deciding on one and dispatching the
    # other produces a verdict about a request nobody made -- here, a turn the
    # parsed copy says is unanchored and the frame says continues the thread.
    verdict = decide_relocation(
        _durable_transcript(
            transport,
            payload={"model": "gpt-5.4", "input": [_user("stale parse")]},
            current_request_text=_frame(_DELTA_PAYLOAD),
        ),
    )

    assert verdict.source == "durable_transcript"
    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello"), _assistant("hi there"), _user("and now?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_frame_the_transport_cannot_have_sent_yields_no_body(transport: RelocationTransport) -> None:
    verdict = decide_relocation(_durable_transcript(transport, current_request_text="{not json"))

    assert verdict.movable is False
    assert verdict.body is None
    assert verdict.decline_reason == "no_account_neutral_body"


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
    verdict = decide_relocation(_client_input(transport, routing_strategy="usage_weighted"))

    assert verdict.movable is True


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    ("ownership_fact", "expected_reason"),
    [
        ({"routing_strategy": "single_account"}, "single_account_routing"),
        ({"input_file_pinned": True}, "input_file_pinned"),
        ({"turn_state_owned": True}, "turn_state_owned"),
        ({"session_identity_bound": True}, "session_identity_bound"),
    ],
)
@pytest.mark.parametrize(
    "insufficient_evidence",
    [
        {"evidence": "none"},
        {"evidence": "ambiguous", "spooled_event_count": 1},
        {"evidence": "ambiguous", "seconds_since_dispatch": None},
    ],
)
def test_an_ownership_fact_is_reported_before_any_evidence_is_consulted(
    transport: RelocationTransport,
    ownership_fact: dict[str, object],
    expected_reason: RelocationDeclineReason,
    insufficient_evidence: dict[str, object],
) -> None:
    # Both gates would decline, so only their order decides what the operator
    # is told. An ownership fact is a binding no request body can neutralize;
    # reporting the evidence instead sends whoever reads the decision looking
    # for a failure that was never the reason this turn could not move.
    verdict = decide_relocation(_client_input(transport, **ownership_fact, **insufficient_evidence))

    assert verdict.movable is False
    assert verdict.decline_reason == expected_reason


def test_concurrent_ownership_facts_report_the_most_binding_one() -> None:
    verdict = decide_relocation(
        _client_input(
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


class _PoisonedTranscript(list[object]):
    """A transcript that cannot be read without saying so."""

    def __iter__(self) -> Any:
        raise AssertionError("the transcript was walked before the evidence gate ran")


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    ("insufficient_evidence", "expected_reason"),
    [
        ({"evidence": "none"}, "no_relocation_evidence"),
        ({"spooled_event_count": 1}, "upstream_execution_observed"),
        ({"evidence": "ambiguous", "seconds_since_dispatch": None}, "outside_ambiguity_window"),
        (
            {"evidence": "ambiguous", "seconds_since_dispatch": 1.0, "arms_side_effect_replay_dedupe": False},
            "no_side_effect_replay_dedupe",
        ),
    ],
)
def test_the_evidence_gate_runs_before_any_body_is_assembled(
    transport: RelocationTransport,
    insufficient_evidence: dict[str, object],
    expected_reason: RelocationDeclineReason,
) -> None:
    # Both halves would decline, so only the order decides which reason the
    # operator is told and whether the chain was walked to learn it. Building
    # the body first would report its problem and hide the evidence's.
    verdict = decide_relocation(
        _durable_transcript(transport, durable_transcript=_PoisonedTranscript([object()]), **insufficient_evidence),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == expected_reason
    assert verdict.body is None
    assert verdict.requires_recovery_fence is False


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_rebuilt_body_still_carries_the_message_the_client_just_sent(
    transport: RelocationTransport,
) -> None:
    # "hello" is the client's own new message and only coincides with the
    # message that opened the thread. A verdict that calls the turn movable
    # while its body has dropped that message is the worst outcome available:
    # the replacement account answers a conversation the user never wrote.
    verdict = decide_relocation(
        _durable_transcript(
            transport,
            payload={**_DELTA_PAYLOAD, "input": [_user("hello"), _user("and now?")]},
        ),
    )

    assert verdict.movable is True
    assert verdict.body is not None
    assert verdict.body["input"] == [
        _user("hello"),
        _assistant("hi there"),
        _user("hello"),
        _user("and now?"),
    ]


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
            seconds_since_dispatch=1.0,
            arms_side_effect_replay_dedupe=True,
        ),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "absent_transcript"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_production_resend_no_account_can_serve_stays_owner_bound(
    transport: RelocationTransport,
) -> None:
    # A real Codex resend restates every item the owner account minted, ids and
    # reasoning included. The join carries it whole, as it must, and the strict
    # predicate then refuses it: the turn stays where it is rather than moving
    # on a body edited to fit.
    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload=_PRODUCTION_FULL_RESEND_PAYLOAD,
            evidence="definitive",
            durable_transcript=_TRANSCRIPT,
        ),
    )

    assert verdict.movable is False
    assert verdict.source is None
    assert verdict.body is None
    assert verdict.decline_reason == "no_account_neutral_body"


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "unusable_input",
    [
        # Bookkeeping only the owner account minted, which no other account resolves.
        [{"type": "reasoning", "id": "rs_owner", "summary": []}, _user("and now?")],
        # Not a list of items at all: a scalar prompt cannot carry the
        # conversation the dropped anchor stood for.
        "and now?",
        [],
    ],
)
def test_a_current_turn_the_rebuild_cannot_use_refuses_rather_than_guessing(
    transport: RelocationTransport,
    unusable_input: JsonValue,
) -> None:
    verdict = decide_relocation(
        _durable_transcript(transport, payload={**_DELTA_PAYLOAD, "input": unusable_input}),
    )

    assert verdict.movable is False
    assert verdict.source is None
    assert verdict.body is None
    assert verdict.decline_reason == "no_account_neutral_body"


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
        _client_input(transport, payload={**_UNANCHORED_PAYLOAD, **unrelocatable_state}),
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
    verdict = decide_relocation(_client_input(transport, **_AMBIGUITY_CLEARED, **execution_evidence))

    assert verdict.movable is False
    assert verdict.decline_reason == "upstream_execution_observed"
    assert verdict.requires_recovery_fence is False


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("blank_response_id", [None, "", "  "])
def test_a_blank_response_id_is_not_proof_that_upstream_ran_the_turn(
    transport: RelocationTransport,
    blank_response_id: str | None,
) -> None:
    verdict = decide_relocation(_client_input(transport, **_AMBIGUITY_CLEARED, response_id=blank_response_id))

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
        _client_input(transport, evidence="ambiguous", seconds_since_dispatch=seconds_since_dispatch),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "outside_ambiguity_window"


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_the_ambiguity_window_boundary_still_relocates(transport: RelocationTransport) -> None:
    verdict = decide_relocation(
        _client_input(
            transport,
            evidence="ambiguous",
            seconds_since_dispatch=RELOCATION_AMBIGUITY_WINDOW_SECONDS,
            arms_side_effect_replay_dedupe=True,
        ),
    )

    assert verdict.movable is True
    assert verdict.requires_recovery_fence is True


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_definitive_evidence_still_requires_that_upstream_emitted_nothing(
    transport: RelocationTransport,
) -> None:
    verdict = decide_relocation(_client_input(transport, spooled_event_count=1))

    assert verdict.movable is False
    assert verdict.decline_reason == "upstream_execution_observed"
    assert verdict.body is None


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_a_recorded_response_id_gates_both_lanes(transport: RelocationTransport) -> None:
    # Both classes owe proof that upstream emitted no response event, and the
    # transports keep that proof in different places: the bridge spools the
    # events, the streaming and WebSocket paths record the id upstream assigned
    # at ``response.created``. Reading only the spool lets the two transports
    # that record the id relocate a turn upstream has already answered.
    acknowledged = {"response_id": "resp_owner_ack", "spooled_event_count": 0}

    fenced = decide_relocation(_client_input(transport, **_AMBIGUITY_CLEARED, **acknowledged))
    unfenced = decide_relocation(_client_input(transport, **acknowledged))

    assert (fenced.movable, fenced.decline_reason) == (False, "upstream_execution_observed")
    assert (unfenced.movable, unfenced.decline_reason) == (False, "upstream_execution_observed")


@pytest.mark.parametrize("transport", _TRANSPORTS)
def test_definitive_evidence_needs_neither_the_window_nor_the_dedupe_contract(
    transport: RelocationTransport,
) -> None:
    verdict = decide_relocation(
        _client_input(
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
def test_an_anchored_delta_is_never_dispatched_by_itself(transport: RelocationTransport) -> None:
    # The anchor names state the replacement account will not have and the
    # client is not supplying. Stripping it and sending the new turn alone would
    # discard the conversation and still report the turn as moved.
    verdict = decide_relocation(
        RelocationInputs(transport=transport, payload=_DELTA_PAYLOAD, evidence="definitive"),
    )

    assert verdict.movable is False
    assert verdict.source is None
    assert verdict.decline_reason == "absent_transcript"


@pytest.mark.parametrize("blank_anchor", [None, "", "   "])
def test_a_blank_anchor_names_no_prior_response(blank_anchor: str | None) -> None:
    # Nothing is owed and nothing is joined: the dispatched frame carries no
    # anchor, so the replacement account sees what the owner account would have.
    payload: dict[str, JsonValue] = {**_UNANCHORED_PAYLOAD, "previous_response_id": blank_anchor}
    verdict = decide_relocation(_client_input("http_bridge", payload=payload, durable_transcript=_TRANSCRIPT))

    assert verdict.source == "client_input"
    assert verdict.body is not None
    assert verdict.body["input"] == [_user("hello")]


_RESENT_HISTORY: list[JsonValue] = [_user("hello"), _user("and now?"), _user("and then?")]


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "carried_alongside",
    [
        # Nothing beside the request.
        {},
        # The bridge's parsed copy of an earlier turn, still naming an anchor.
        # The frame is what would go upstream, and it is unanchored; a decision
        # taken on the copy relocates a body the dispatch does not carry.
        {"payload": _DELTA_PAYLOAD},
    ],
)
def test_a_resend_naming_no_prior_response_is_not_joined_to_the_chain(
    transport: RelocationTransport,
    carried_alongside: dict[str, object],
) -> None:
    # The client restated its whole history, and that history happens to hold no
    # model-authored item -- a thread of questions, or one whose answers the
    # client does not keep. The request names no prior response, so the chain a
    # caller loaded for some other turn is not its history, and the body that
    # goes upstream is the one the client wrote.
    verdict = decide_relocation(
        _client_input(
            transport,
            current_request_text=_frame({"model": "gpt-5.4", "input": _RESENT_HISTORY}),
            durable_transcript=_TRANSCRIPT,
            **carried_alongside,
        ),
    )

    assert verdict.movable is True
    assert verdict.source == "client_input"
    assert verdict.body is not None
    assert verdict.body["input"] == _RESENT_HISTORY


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
            _client_input("http_bridge", downstream_output_visible=True),
            _client_input("http_bridge", routing_strategy="single_account"),
            _client_input("http_bridge", input_file_pinned=True),
            _client_input("http_bridge", turn_state_owned=True),
            _client_input("http_bridge", session_identity_bound=True),
            _client_input("http_bridge", evidence="none"),
            _durable_transcript("http_bridge", durable_transcript=None),
            _durable_transcript("http_bridge", durable_transcript=(_Turn(operation=_Operation(None), events=()),)),
            _client_input("http_bridge", **_AMBIGUITY_CLEARED, spooled_event_count=1),
            _client_input("http_bridge", evidence="ambiguous", arms_side_effect_replay_dedupe=True),
            _client_input("http_bridge", evidence="ambiguous", seconds_since_dispatch=1.0),
        )
    }

    assert reasons == _DECLINE_REASONS


def _wire_user(text: str) -> dict[str, JsonValue]:
    """A user message as the wire carries it, without the ``type`` this file's tidier helper adds."""

    return {"role": "user", "content": [{"type": "input_text", "text": text}]}


def _owner_answer(text: str, response_id: str) -> dict[str, JsonValue]:
    """That turn's answer as the spool holds it, minted by the account that produced it."""

    return {
        "type": "message",
        "id": f"msg_{response_id}",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _restated_answer(text: str, bookkeeping: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """The same answer as a client sends it back, under whichever recording fields it kept.

    ``status``, ``phase`` and the turn metadata are all optional on the wire and
    all say how the owner account recorded the answer rather than what it said,
    so every spelling below names the same exchange.
    """

    return {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": text}],
        **bookkeeping,
    }


_WIRE_CHAIN = tuple(
    _Turn(
        operation=_Operation(
            request_text=_frame({"model": "gpt-5.4", "input": [_wire_user(f"q{index}")]}),
            response_id=f"resp_{index}",
            parent_response_id=None if index == 1 else f"resp_{index - 1}",
        ),
        events=(
            _sse(
                {
                    "type": "response.completed",
                    "response": {"id": f"resp_{index}", "output": [_owner_answer(f"a{index}", f"resp_{index}")]},
                }
            ),
        ),
    )
    for index in range(1, 5)
)


def _wire_full_resend(*bookkeeping: dict[str, JsonValue]) -> list[JsonValue]:
    """The whole four-turn thread restated, each answer carrying the recording fields named for it."""

    resend: list[JsonValue] = []
    for index, fields in enumerate(bookkeeping, start=1):
        resend.extend((_wire_user(f"q{index}"), _restated_answer(f"a{index}", fields)))
    resend.append(_wire_user("q5"))
    return resend


_RECORDED: dict[str, JsonValue] = {"status": "completed"}
_FINAL: dict[str, JsonValue] = {"phase": "final_answer"}
_TURN_METADATA: dict[str, JsonValue] = {"internal_chat_message_metadata_passthrough": {"turn_id": "turn-restated"}}


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize(
    "bookkeeping",
    [
        # The spelling the spool records, then the bare one the wire also allows.
        (_RECORDED,) * 4,
        ({},) * 4,
        (_RECORDED, _RECORDED, _RECORDED, {}),
        # ``phase`` rides on the assistant message a Codex client actually
        # resends -- it is on this file's own production full-resend fixture.
        (_FINAL,) * 4,
        ({**_RECORDED, **_FINAL},) * 4,
        (_TURN_METADATA,) * 4,
        ({}, _FINAL, {**_RECORDED, "phase": "commentary"}, {**_TURN_METADATA, **_FINAL}),
    ],
)
def test_a_legal_field_difference_does_not_double_the_conversation(
    transport: RelocationTransport,
    bookkeeping: tuple[dict[str, JsonValue], ...],
) -> None:
    # Every one of these fields says how the owner account recorded the answer
    # rather than what it said, and a client restating a turn need not echo any
    # of them. A comparison that reads one as content makes the restatement look
    # like new material, so the chain is kept as well and every turn is
    # dispatched twice -- on the fenced lane, at the cost of the one relocation
    # that operation will ever get. Two earlier readings of this key each lost
    # to a different one of them, which is why it names none of them.
    resend = _wire_full_resend(*bookkeeping)

    verdict = decide_relocation(
        RelocationInputs(
            transport=transport,
            payload={"model": "gpt-5.4", "input": resend, "previous_response_id": "resp_4"},
            evidence="definitive",
            durable_transcript=_WIRE_CHAIN,
        ),
    )

    assert verdict.movable is True
    assert verdict.source == "client_input"
    assert verdict.body is not None
    assert verdict.body["input"] == resend


@pytest.mark.parametrize("transport", _TRANSPORTS)
@pytest.mark.parametrize("empty_input", [[], "", "   "])
def test_a_request_with_nothing_to_send_is_not_relocated(
    transport: RelocationTransport,
    empty_input: JsonValue,
) -> None:
    # An empty body is account-neutral by every structural reading, so it moves
    # unless it is refused. The dispatch can only come back as an invalid
    # request, and on the ambiguous lane it has spent the one-shot budget that
    # covers this operation for the whole of its retention -- so the recovery the
    # conversation will actually need is gone before it is asked for.
    verdict = decide_relocation(
        _client_input(
            transport,
            payload={"model": "gpt-5.4", "input": empty_input},
            **_AMBIGUITY_CLEARED,
        ),
    )

    assert verdict.movable is False
    assert verdict.decline_reason == "no_account_neutral_body"
    assert verdict.requires_recovery_fence is False
    assert verdict.body is None


def test_a_relocatable_verdict_never_carries_a_decline_reason() -> None:
    verdict = decide_relocation(_client_input("websocket"))

    assert (verdict.movable, verdict.decline_reason) == (True, None)
    assert set(get_args(RelocationSource)) == set(_SOURCE_BUILDERS)


# --- The transcript bounds, reached the way production reaches them -----------
#
# The rebuild takes its three bounds as default arguments. Every bound assertion
# that passes them explicitly leaves the defaults -- the only values production
# ever uses, because this decision does not offer to override them -- asserted by
# nothing, so a widened default is a bound that no longer exists and a green
# suite. These rows go through the same entry point every transport calls, on
# material sized against the shipped values.


def _linked_chain(
    turn_count: int,
    *,
    prompt: str = "q",
    answer: str = "a",
    stored_items: int = 1,
) -> tuple[_Turn, ...]:
    return tuple(
        _completed_turn(
            prompt,
            f"{answer}{index}",
            response_id=f"resp_{index}",
            parent_response_id=None if index == 0 else f"resp_{index - 1}",
            stored_input=[_user(f"{prompt}{index}-{position}") for position in range(stored_items)],
        )
        for index in range(turn_count)
    )


def _relocation_of(chain: tuple[_Turn, ...]) -> RelocationVerdict:
    """The verdict for a delta continuing ``chain``, with the bounds production runs."""

    return decide_relocation(
        RelocationInputs(
            transport="http_bridge",
            payload={
                "model": "gpt-5.4",
                "input": [_user("next")],
                "previous_response_id": chain[-1].operation.response_id,
            },
            evidence="definitive",
            durable_transcript=chain,
        )
    )


def test_the_shipped_turn_bound_refuses_one_turn_past_it() -> None:
    assert _relocation_of(_linked_chain(RELOCATION_TRANSCRIPT_MAX_TURNS)).movable is True

    refused = _relocation_of(_linked_chain(RELOCATION_TRANSCRIPT_MAX_TURNS + 1))

    assert (refused.movable, refused.decline_reason) == (False, "no_account_neutral_body")


def test_the_shipped_byte_bound_refuses_a_transcript_that_exceeds_it_in_total() -> None:
    # Three turns of roughly 3 MiB: each is well inside the 8 MiB bound on its
    # own and the three together are not, so this also fails a bound re-read per
    # turn rather than spent across the walk.
    third_of_the_bound = RELOCATION_TRANSCRIPT_MAX_BYTES // 3
    filler = "x" * (third_of_the_bound // 2)

    assert _relocation_of(_linked_chain(2, prompt=filler, answer=filler)).movable is True

    refused = _relocation_of(_linked_chain(3, prompt=filler, answer=filler))

    assert (refused.movable, refused.decline_reason) == (False, "no_account_neutral_body")


def test_the_shipped_item_bound_refuses_a_transcript_that_exceeds_it_in_total() -> None:
    # Three turns of twelve thousand items. A legal item is about fifty bytes, so
    # the whole transcript is inside the byte bound and inside the turn bound
    # while carrying more items than the rebuild will do per-item work for.
    items_per_turn = RELOCATION_TRANSCRIPT_MAX_ITEMS // 3 + 1

    assert _relocation_of(_linked_chain(3, stored_items=2)).movable is True

    refused = _relocation_of(_linked_chain(3, stored_items=items_per_turn))

    assert (refused.movable, refused.decline_reason) == (False, "no_account_neutral_body")
