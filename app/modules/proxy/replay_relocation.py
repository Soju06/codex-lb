"""One transport-independent verdict on moving an anchored turn to another account."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    AccountNeutralReplayProjection,
    project_durable_transcript_for_account_neutral_fresh_replay,
    project_responses_input_for_account_neutral_fresh_replay,
    responses_input_suffix_matches_pending_tool_calls,
    responses_input_suffix_retains_prior_output,
    responses_payload_is_account_neutral_fresh_replay,
)

RelocationTransport = Literal["http_stream", "websocket", "http_bridge"]
"""Which request path asked. No branch below reads it: that identity is the point."""

RelocationEvidence = Literal["definitive", "ambiguous", "none"]
"""What the failure proved about upstream acceptance.

``definitive`` is an upstream quota or usage-limit rejection, or a confirmed
pre-dispatch transport failure: upstream accepted nothing, so another account is
a first attempt rather than a retry. ``ambiguous`` is a dispatch that left and
then died before any event, which may or may not have run. ``none`` covers every
other outcome, including a deterministic rejection another account would repeat.
"""

RelocationSource = Literal["client_full_resend", "durable_transcript", "unanchored"]
RelocationDeclineReason = Literal[
    "downstream_output_visible",
    "single_account_routing",
    "input_file_pinned",
    "turn_state_owned",
    "session_identity_bound",
    "no_relocation_evidence",
    "absent_transcript",
    "no_account_neutral_body",
    "upstream_execution_observed",
    "outside_ambiguity_window",
    "no_side_effect_replay_dedupe",
]

# An eventless dispatch older than this is likelier to be mid-execution and about
# to write its first event than to have been lost, and relocating it would cost a
# second generation of the same turn. The Settings ratchet is full and this is a
# transport invariant, not an operator knob.
RELOCATION_AMBIGUITY_WINDOW_SECONDS = 90.0

_SINGLE_ACCOUNT_ROUTING_STRATEGY = "single_account"


@dataclass(frozen=True, slots=True)
class ClientResendProof:
    """Evidence that the client's own prefix reproduces the anchor's stored input.

    Establishing it needs the durable or continuity row the anchor points at, so
    it is settled at the I/O boundary and the verdict consumes only its result:
    how many leading items the stored turn accounted for, and the tool-call
    manifest that turn left outstanding.
    """

    stored_input_item_count: int
    pending_tool_calls: Mapping[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RelocationInputs:
    """Everything the shared decision is allowed to look at."""

    transport: RelocationTransport
    payload: Mapping[str, JsonValue]
    evidence: RelocationEvidence
    previous_response_id: str | None = None
    downstream_output_visible: bool = False
    routing_strategy: str | None = None
    input_file_pinned: bool = False
    turn_state_owned: bool = False
    session_identity_bound: bool = False
    client_resend: ClientResendProof | None = None
    durable_transcript: Sequence[object] | None = None
    current_request_text: str | None = None
    response_id: str | None = None
    spooled_event_count: int = 0
    seconds_since_dispatch: float | None = None
    arms_side_effect_replay_dedupe: bool = False
    """Whether the caller will carry the origin operation's replay-dedupe identity.

    A transport that does not implement that contract cannot suppress a
    side-effecting tool call the origin dispatch may already have emitted, so it
    is barred from the fenced lane rather than offered it with a weaker
    guarantee. Absent by default: a caller that has not said it arms the dedupe
    has not.
    """


@dataclass(frozen=True, slots=True)
class RelocationVerdict:
    """Whether the turn may move, on which body, and why not when it may not."""

    movable: bool
    body: Mapping[str, JsonValue] | None = None
    source: RelocationSource | None = None
    requires_recovery_fence: bool = False
    decline_reason: RelocationDeclineReason | None = None


def decide_relocation(inputs: RelocationInputs) -> RelocationVerdict:
    """Answer "may this anchored request move, and with what body?" for every transport.

    The order is the contract. Downstream-visible output ends eligibility before
    anything is consulted, because a client that already holds part of the turn
    cannot be served a second one. Ownership facts come next: no request body
    neutralizes them, so evaluating a body first would only produce a verdict the
    facts then overturn. Evidence gates the rest, and only then does the source
    ladder look for a body another account can accept.
    """

    if inputs.downstream_output_visible:
        return _decline("downstream_output_visible")
    ownership_reason = _ownership_decline_reason(inputs)
    if ownership_reason is not None:
        return _decline(ownership_reason)
    evidence_reason = _evidence_decline_reason(inputs)
    if evidence_reason is not None:
        return _decline(evidence_reason)
    relocated = _relocated_body(inputs)
    if isinstance(relocated, str):
        return _decline(relocated)
    body, source = relocated
    return RelocationVerdict(
        movable=True,
        body=body,
        source=source,
        requires_recovery_fence=inputs.evidence == "ambiguous",
    )


def _decline(reason: RelocationDeclineReason) -> RelocationVerdict:
    return RelocationVerdict(movable=False, decline_reason=reason)


def _ownership_decline_reason(inputs: RelocationInputs) -> RelocationDeclineReason | None:
    """The binding that outlives every request body, or ``None`` when none does."""

    if inputs.routing_strategy == _SINGLE_ACCOUNT_ROUTING_STRATEGY:
        return "single_account_routing"
    if inputs.input_file_pinned:
        return "input_file_pinned"
    if inputs.turn_state_owned:
        return "turn_state_owned"
    if inputs.session_identity_bound:
        return "session_identity_bound"
    return None


def _evidence_decline_reason(inputs: RelocationInputs) -> RelocationDeclineReason | None:
    """What the failure proved, and what each evidence class still owes.

    Both classes owe the same first proof. A spooled event or a recorded
    response id means upstream executed the turn: the definitive class is
    defined as a rejection upstream accepted nothing from, so that is not what
    happened, and the ambiguous class is then not ambiguous but known-and-run.
    The definitive lane consumes no claim, so nothing further down would catch
    it.

    The ambiguous class owes two more. An age the caller could not establish, or
    one no clock could have produced, counts as outside the window rather than
    inside it -- a dispatch old enough to be mid-execution is likelier to write
    its first event than to have been lost. And without the side-effect
    replay-dedupe identity on the relocated dispatch, the duplicate this lane
    knowingly risks is unbounded in kind rather than merely in tokens.
    """

    if inputs.evidence == "none":
        return "no_relocation_evidence"
    if inputs.spooled_event_count > 0 or (inputs.response_id or "").strip():
        return "upstream_execution_observed"
    if inputs.evidence != "ambiguous":
        return None
    seconds_since_dispatch = inputs.seconds_since_dispatch
    if seconds_since_dispatch is None or not 0.0 <= seconds_since_dispatch <= RELOCATION_AMBIGUITY_WINDOW_SECONDS:
        return "outside_ambiguity_window"
    if not inputs.arms_side_effect_replay_dedupe:
        return "no_side_effect_replay_dedupe"
    return None


def _relocated_body(
    inputs: RelocationInputs,
) -> tuple[Mapping[str, JsonValue], RelocationSource] | RelocationDeclineReason:
    """The first source that yields a body another account can accept, or why none did.

    The ladder is ordered by how much the proxy has to assume. A client full
    resend is the client's own transcript; the durable chain is one the proxy
    rebuilds from what it spooled; an unanchored body needs nothing proven at all
    and is last only because the anchored sources answer the anchored question.

    An anchored turn whose transport recorded no durable material declines with
    its own reason. Having nothing to rebuild from is a different fact from a
    rebuild that ran and could not prove itself, and only the second one says
    anything about this conversation.
    """

    client_resend_body = _client_full_resend_body(inputs)
    if client_resend_body is not None:
        return client_resend_body, "client_full_resend"
    if not _is_anchored(inputs):
        unanchored_body = _account_neutral_body_without_anchor(inputs.payload)
        return (unanchored_body, "unanchored") if unanchored_body is not None else "no_account_neutral_body"
    transcript = inputs.durable_transcript
    if not transcript:
        return "absent_transcript"
    durable_transcript_body = project_durable_transcript_for_account_neutral_fresh_replay(
        transcript,
        current_request_text=_current_request_text(inputs),
    )
    if durable_transcript_body is None:
        return "no_account_neutral_body"
    return durable_transcript_body, "durable_transcript"


def _client_full_resend_body(inputs: RelocationInputs) -> Mapping[str, JsonValue] | None:
    """The client's own resend, once its suffix is proven to continue the stored turn.

    Two projections, and the difference between them is the whole point. The
    proof runs on one that keeps inline Responses-Lite developer ids, because
    the suffix checks have to see them to reject a response-owned message. The
    dispatched body takes the default one, which strips response-owned ids and
    drops reasoning: a real resend restates every item the owner account minted,
    bookkeeping and all, and the strict predicate refuses each of them.
    """

    proof = inputs.client_resend
    input_value = inputs.payload.get("input")
    if not _is_anchored(inputs) or proof is None or not isinstance(input_value, list):
        return None
    input_items = cast(list[JsonValue], input_value)
    classification = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=proof.stored_input_item_count,
        preserve_developer_message_ids=True,
    )
    if classification is None or not _resend_suffix_continues_the_stored_turn(classification, proof):
        return None
    replay_projection = project_responses_input_for_account_neutral_fresh_replay(
        input_items,
        stored_count=proof.stored_input_item_count,
    )
    if replay_projection is None:
        return None
    return _account_neutral_body_without_anchor(
        {**inputs.payload, "input": cast(JsonValue, replay_projection.input_items)}
    )


def _resend_suffix_continues_the_stored_turn(
    projection: AccountNeutralReplayProjection,
    proof: ClientResendProof,
) -> bool:
    """Whether what the client added past the stored turn stands on its own."""

    pending_tool_calls = proof.pending_tool_calls
    return responses_input_suffix_retains_prior_output(
        projection.input_items,
        stored_count=projection.stored_prefix_count,
        canonical_lite_developer_index=projection.canonical_lite_developer_index,
    ) or (
        pending_tool_calls is not None
        and responses_input_suffix_matches_pending_tool_calls(
            projection.input_items,
            stored_count=projection.stored_prefix_count,
            pending_tool_calls=pending_tool_calls,
            canonical_lite_developer_index=projection.canonical_lite_developer_index,
        )
    )


def _account_neutral_body_without_anchor(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    body = {key: value for key, value in payload.items() if key != "previous_response_id"}
    return body if responses_payload_is_account_neutral_fresh_replay(body) else None


def _is_anchored(inputs: RelocationInputs) -> bool:
    """Whether this turn names a prior response, in the dedicated field or in the body.

    The field is a mirror the caller fills in; the body is where the anchor
    actually lives. Trusting the mirror alone lets an unmirrored anchor take the
    unanchored rung, which strips ``previous_response_id`` and dispatches the new
    turn by itself -- the conversation discarded, and the verdict calling it
    movable.
    """

    return _names_a_prior_response(inputs.previous_response_id) or _names_a_prior_response(
        inputs.payload.get("previous_response_id")
    )


def _names_a_prior_response(value: JsonValue | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _current_request_text(inputs: RelocationInputs) -> str | None:
    """The client's current turn as the text the rebuild joins the chain to.

    The session bridge holds the exact frame it would have sent upstream and
    passes it verbatim; the streaming and WebSocket paths hold a parsed body, so
    serialize theirs rather than making the durable source bridge-only.
    """

    if inputs.current_request_text is not None:
        return inputs.current_request_text
    try:
        return json.dumps(dict(inputs.payload), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
