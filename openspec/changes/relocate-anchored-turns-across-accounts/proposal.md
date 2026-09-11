## Why

A Codex continuation does not resend its conversation. It sends the new turn
plus `previous_response_id`, and that identifier is owned by the account that
produced it. When that account is exhausted, suspended, or gone, the anchor is
worthless on every other account, so the proxy fails the turn closed —
`previous_response_owner_unavailable` 502 on the bridge, the equivalent
`response.failed` on the stream path. The conversation dies even though the
pool has usable accounts and we hold the material to rebuild it.

We already hold that material. `http_bridge_operations.request_text` plus the
ordered terminal SSE spool plus `parent_response_id` is a complete parent chain,
and `DurableBridgeRepository.get_replayable_transcript` already walks it
fail-closed with `max_turns` and `max_bytes` bounds. It has **zero production
callers**: `drop-bridge-recovery-modes` (#2336) deleted the request-path caller
and left the storage layer in place.

The payload-only half of this is already specified and already works: "Verified
full resend can recover from selection-time owner loss" covers a client that
resends its whole history. The gap is the ordinary case — a **delta resend**,
where the client sends only the new turn and the anchor, and the proxy has no
client-supplied history to verify.

## How much this is worth

Measured on the production fleet (beta.7), 7 days, requests that died with no
response event while carrying a conversation:

| evidence class | dead turns | distinct conversations | per day |
|---|---|---|---|
| ambiguous (`stream_incomplete`, `upstream_request_timeout`, `stream_idle_timeout`) | 2,352 | 993 | 336 |
| definitive (`previous_response_owner_unavailable`) | 1,883 | 367 | 269 |

Two things follow. The ambiguous class is not a tail case — it touches roughly
three times more distinct conversations than the definitive one, so covering
only definitive evidence would leave most of the problem in place. And the
definitive class concentrates 1,883 turns onto 367 conversations, about five
each: a client that keeps retrying a dead anchor collects a fresh 502 every
time, which is what a conversation "dying" actually looks like from the client
side.

Upper-bound caveat: some eventless `stream_incomplete` rows are probably
confirmed pre-dispatch failures, which belong to the definitive class and need
no fence. The request log cannot separate them, so 2,352 is a ceiling — not one
that changes the ordering.

**Coverage caveat, and it is the important one.** Split by transport, those same
dead turns are overwhelmingly downstream-WebSocket: 2,151 of the definitive
class and 1,768 of the ambiguous class, against 30 and 622 on the HTTP path.
`DurableBridgeRepository.record_operation` has exactly one caller,
`app/modules/proxy/_service/http_bridge/request_submit.py`, so the durable
material this change rebuilds from exists **only for HTTP session bridge
turns**. The WebSocket path imports the coordinator for owner lookup alone. The
spool itself is healthy where it is written — 33,920 operations, every one with
a stored request body, 96.6% with a complete event spool — but it does not cover
the majority of the deaths. This change therefore fixes the bridge lane and
leaves the larger WebSocket lane for the follow-up named in Out of scope. Saying
otherwise would be claiming a fix for traffic that has no material to recover
from.

Separately worth recording: `upstream_operation_status_unknown` fired **zero**
times in that window. The bounded 503 that is nominally the fail-closed terminal
for an ambiguous operation is not what production reaches; these turns surface
to the client as a broken `stream_incomplete` stream.

The owner decided (2026-09-11) to close that gap and to cover both evidence
classes:

- **① definitive pre-dispatch rejection** — a quota/usage-limit rejection, or a
  confirmed pre-dispatch transport failure, with zero response events. Upstream
  provably accepted nothing, so re-dispatching elsewhere is not a duplicate.
- **② ambiguous eventless dispatch** — the frame left, the connection died
  before any event, and we cannot prove whether upstream ran it.

② is an at-least-once semantic and this change deliberately partially reverses
`aae61f6f3` (#2336, `drop-bridge-recovery-modes`), whose proposal states there
is "no replacement, by design". The owner accepts the residual duplicate-token
cost with the fences below in place. It is **not** restored as the four-valued
`..._AMBIGUOUS_CONTINUATION_RECOVERY_MODE` selector — that setting stays
deleted, and this is one behaviour with no operator knob.

## What Changes

- **One relocation decision, one place.** A new pure module
  `app/modules/proxy/replay_relocation.py` answers "may this anchored request
  move, and with what body?" for every transport. Today that question is
  answered by five independent implementations (`_service/streaming/retry.py`,
  `_service/http_bridge/streaming.py` twice, `_service/compact.py`,
  `_service/websocket/helpers.py`) which already disagree in scope. A single
  I/O wrapper in `_service/support.py` loads the transcript and emits one
  structured decision log line.
- **① relocation on a rebuilt transcript.** When the evidence is definitive and
  the durable chain is complete, the proxy rebuilds the full conversation from
  the spool, drops the anchor, dedupes any overlap with the client's own
  suffix, and runs the result through the **same** strict
  `responses_payload_is_account_neutral_fresh_replay` predicate a client full
  resend must pass. Any gap — a missing request body, an incomplete spool, a
  broken parent chain, an unsettled tool call, a non-portable tool, account-owned
  state — fails closed to today's behaviour.
- **② relocation behind the existing fence, at most once per operation.** The
  already-specified "Fenced one-shot recovery dispatch" primitives gain a live
  caller and a stated bound of one dispatch per operation for its entire
  retention. Three further preconditions: zero spooled events and no response
  id (any event is proof upstream ran it, which makes the outcome known rather
  than ambiguous), a bounded ambiguity window, and the side-effect replay
  dedupe armed on the relocated dispatch so a duplicated tool call is
  suppressed rather than executed.
- **No lenient fallback.** The downstream fork forwards an unclassifiable
  payload to the new account when its strict projection declines. We do not: a
  body we cannot classify stays owner-bound.

## Impact

- Affected capability: `responses-api-compat`. Four ADDED requirements, two
  MODIFIED (`Durable replay is limited to ambiguous transport outcomes`,
  `Fenced one-shot recovery dispatch`). No REMOVED.
- **Clients**: an anchored conversation whose owner becomes unavailable is
  served by another account instead of terminating. The client sees a slower
  turn, not a dead thread.
- **Cost**: a relocated turn re-sends the rebuilt conversation, and under ② a
  turn upstream may have already run can run a second time. The duplicate is
  bounded at one per operation. It cannot duplicate tool side effects **on a
  transport that implements the side-effect replay-dedupe contract**, which
  today is the HTTP session bridge and only the bridge
  (`app/modules/proxy/tool_call_dedupe.py` is wired from
  `_service/http_bridge/request_submit.py` and nowhere else). That is why the
  fenced lane is restricted to transports carrying that contract: elsewhere the
  duplicate would be unbounded in kind, not merely in tokens. What remains on
  the bridge is duplicated model tokens and duplicated upstream-hosted tool
  execution.
- **Operators**: no new setting. The bound, the ambiguity window and the
  transcript caps are module constants. `[settings_fields].max` stays 96.
- **Depends on the durable recovery primitives staying in the tree.** #2366
  (`retire-recovery-dispatch-storage`) removed
  `claim_unknown_operation_for_recovery`, the `recovery_dispatch_count` ORM
  mapping and the `expected_recovery_dispatch_count` CAS predicates on the
  grounds that they were callerless; #2383 restored them. This change is the
  caller that makes them reachable, and the retirement must not be reattempted
  while it stands: the claim is a serialized `FOR UPDATE` over the session and
  operation rows, and it is what makes concurrent reconnects unable to both
  win.

## Out of scope

- **The WebSocket lane, which is where most of the deaths are.** Covering it
  needs an equivalent durable record on the WebSocket path — the same request
  body, terminal event spool and parent link the bridge already writes — plus
  the side-effect replay-dedupe contract, which is likewise bridge-only today.
  Both are substantial pieces of work with their own failure modes, and neither
  is a variation on this change; they are a prerequisite for extending it. This
  change must not be read as fixing them.
- **Compact.** "Compact requests recover from quota-caused previous-response
  owner loss" carries an explicit carve-out ("the durable prefix metadata that
  could prove it is deliberately not consulted here"). Reversing that paragraph
  is a follow-up change so this one stays one concern wide; the compact path
  keeps today's behaviour meanwhile.
- Widening the pool walk itself — `walk-account-pool-before-surfacing-429`
  decides whether a request may walk; this change widens which requests can.
