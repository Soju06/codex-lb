"""One transport-independent verdict on moving an anchored turn to another account."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, cast

from app.core.types import JsonValue
from app.modules.proxy.replay_safety import (
    project_durable_transcript_for_account_neutral_fresh_replay,
    responses_payload_is_account_neutral_fresh_replay,
    responses_request_frame_payload,
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

RelocationSource = Literal["durable_transcript", "unanchored"]
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
class RelocationInputs:
    """Everything the shared decision is allowed to look at."""

    transport: RelocationTransport
    payload: Mapping[str, JsonValue]
    evidence: RelocationEvidence
    downstream_output_visible: bool = False
    routing_strategy: str | None = None
    input_file_pinned: bool = False
    turn_state_owned: bool = False
    session_identity_bound: bool = False
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
    """What the failure proved, and what each evidence class owes on top of it.

    Both classes owe proof that upstream emitted no response event, and the two
    records of one are a spooled event and a recorded response id. Which of them
    a transport keeps differs -- the bridge spools, the streaming and WebSocket
    paths record the id -- so reading only one lets the transports that keep the
    other relocate a turn upstream has already answered.

    The fenced lane owes two further facts, because it is spending a one-shot
    budget on a turn that may already have run. An age the caller could not
    establish, or one no clock could have produced, counts as outside the window
    rather than inside it: a dispatch old enough to be mid-execution is likelier
    to write its first event than to have been lost. And without the side-effect
    replay-dedupe identity on the relocated dispatch, the duplicate this lane
    knowingly risks is unbounded in kind rather than merely in tokens.
    """

    if inputs.evidence == "none":
        return "no_relocation_evidence"
    if inputs.spooled_event_count > 0 or _names_a_prior_response(inputs.response_id):
        return "upstream_execution_observed"
    if inputs.evidence == "definitive":
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
    """The source that yields a body another account can accept, or why none did.

    A request carrying no anchor is the whole conversation already and needs
    only the strict predicate. An anchored one is rebuilt from the chain, which
    classifies the client's own turn the same way it classifies every stored
    request in that chain -- so a client that resent its history supersedes the
    chain rather than being joined to it, and the anchor is passed in only so
    the walk can verify it terminates there.

    An anchored turn whose transport recorded no durable material declines with
    its own reason. Having nothing to rebuild from is a different fact from a
    rebuild that ran and could not prove itself, and only the second one says
    anything about this conversation.
    """

    payload = _current_turn_payload(inputs)
    if payload is None:
        return "no_account_neutral_body"
    anchor = payload.get("previous_response_id")
    if not _names_a_prior_response(anchor):
        unanchored_body = _account_neutral_body_without_anchor(payload)
        return (unanchored_body, "unanchored") if unanchored_body is not None else "no_account_neutral_body"
    if not inputs.durable_transcript:
        return "absent_transcript"
    durable_transcript_body = project_durable_transcript_for_account_neutral_fresh_replay(
        inputs.durable_transcript,
        anchor_response_id=cast(str, anchor),
        current_payload=payload,
    )
    if durable_transcript_body is None:
        return "no_account_neutral_body"
    return durable_transcript_body, "durable_transcript"


def _account_neutral_body_without_anchor(payload: Mapping[str, JsonValue]) -> Mapping[str, JsonValue] | None:
    body = {key: value for key, value in payload.items() if key != "previous_response_id"}
    return body if responses_payload_is_account_neutral_fresh_replay(body) else None


def _names_a_prior_response(value: JsonValue | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _current_turn_payload(inputs: RelocationInputs) -> Mapping[str, JsonValue] | None:
    """The one body this verdict is decided from and dispatched as.

    The session bridge holds the exact frame it would have sent upstream, which
    need not agree with the parsed request it also carries; the streaming and
    WebSocket paths hold only the parsed request. Preferring the frame keeps the
    decision and the dispatch on the same material, because a verdict reached
    about one body and carried by another describes a request nobody made -- not
    least about whether that request is anchored at all.
    """

    if inputs.current_request_text is None:
        return inputs.payload
    return responses_request_frame_payload(inputs.current_request_text)
