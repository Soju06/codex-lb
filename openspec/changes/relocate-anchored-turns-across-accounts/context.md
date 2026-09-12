# Context

## What "relocation" means here

A Codex continuation is a delta: one new turn plus `previous_response_id`. That
identifier names a response object owned by the account that produced it, so it
cannot be presented to a different account. Relocation is the proxy rebuilding
the conversation itself — from the request body and terminal response it already
spooled for every parent turn — and dispatching it as an anchor-free request.
The client sees a slower turn instead of a dead thread. The *decision* is
transport-independent and this change gives it one home; the *material* is not,
and today only the HTTP session bridge records it (see "The coverage this change
does not have").

## What the two evidence classes actually cost

**① definitive.** Upstream answered with a quota or usage-limit rejection, or
the dispatch provably never left. Nothing was accepted, so dispatching elsewhere
is a first attempt, not a retry. There is no duplicate to suppress and no budget
to consume — a failed rebuild simply rolls back.

**② ambiguous.** The frame left and the connection died before any event. The
turn may have run. Re-dispatching can therefore cost a second generation of the
same turn: duplicate model tokens, and duplicate execution of any upstream-hosted
tool such as web search.

It does **not** cost duplicate client-side tool execution, for two reasons that
do not hold equally. The first is unconditional: relocation is refused once any
output is downstream-visible, so a tool call the client already executed cannot
be re-emitted by a relocation. The second is conditional: the relocated dispatch
carries the origin operation's side-effect replay-dedupe identity, so a repeated
side-effecting call is suppressed with the dedicated terminal failure — but that
dedupe is wired only on the HTTP session bridge. That asymmetry is why the fenced
lane is restricted to transports that implement it rather than being offered
everywhere with a weaker guarantee.

The residual exposure is therefore bounded at **one duplicated generation per
operation for the whole of its retention**, and the owner accepted that in
exchange for conversations that survive account loss.

## What the production numbers changed about this design

The 7-day measurement in the proposal was run to answer one question: is the
ambiguous class large enough to be worth its fences, or is it a tail case that
carries most of the risk for a sliver of the benefit? It is not a tail case —
993 distinct conversations against 367 for the definitive class.

Two details from that measurement changed the spec rather than just confirming
it.

First, `upstream_operation_status_unknown` fired zero times in the window. The
bounded 503 is real code, but it lives on the HTTP bridge submit path, and the
ambiguous eventless failures that actually happen in production surface on the
streaming path as `stream_incomplete`. So "a refused claim terminates with the
existing 503" was only true for one transport. The requirement now says a
refused claim terminates through whatever fail-closed outcome its own transport
already produces, and forbids the two wrong answers: a second dispatch, or
reporting the refusal as pool exhaustion.

Second, the definitive class concentrates about five dead turns onto each
affected conversation while the ambiguous class averages under two and a half.
That asymmetry is the client retry loop: a thread whose anchor is owned by a
gone account fails identically on every retry, so the same conversation
generates a 502 over and over. It is a useful signal for verification — if the
definitive lane works, the turns-per-conversation ratio for that error code
should collapse toward one before the absolute count does.

## The coverage this change does not have

`DurableBridgeRepository.record_operation` has exactly one caller,
`app/modules/proxy/_service/http_bridge/request_submit.py`. The WebSocket path
imports `DurableBridgeLookup` for owner resolution and one frame-rewriting
helper from the bridge module, and records nothing. So a downstream-WebSocket
turn has no stored request body, no event spool and no parent link — there is
no transcript to rebuild and no operation to claim.

That matters because the deaths are mostly there: of the measured dead turns,
2,151 definitive and 1,768 ambiguous were downstream-WebSocket, against 30 and
622 on the HTTP path. The mechanism in this change is correct and the spool it
reads is healthy where it is written (33,920 operations, all with a stored
request body, 96.6% with a complete event spool) — it simply does not reach the
larger lane.

This is stated in the requirements rather than left implicit: a transport
without durable material must report an absent transcript and keep today's
behaviour, and a transport without the side-effect dedupe contract must not take
the fenced lane at all. Extending either to the WebSocket path is its own piece
of work with its own failure modes, not a variation on this one.

The honest reading of this change is: it fixes the bridge lane, and it makes the
shape of the WebSocket fix obvious.

## Why the join refuses instead of deduplicating

The first three implementations of this rebuild all tried to find the boundary
between the chain and the client's turn by comparing item content, and all three
found a way to delete a message the user had just sent — a leading match that
was a coincidence, then a tail-anchored match that was a coincidence, then the
same class again through a different path. The failure is quiet by construction:
the shortened conversation satisfies every structural predicate, the strict
account-neutral projection accepts it, and no downstream check can notice that
the user's words are missing.

Content equality cannot distinguish "the client is restating this turn" from
"the client happened to send the same words again". A fourth implementation then
showed that inspecting the input for *shape* fails the same way: a predicate
that reads "no model-authored items" as "this is a delta" classified a full
resend as a delta and doubled the conversation at the public entry point.

The signal was in the request all along. `previous_response_id` present means
the input is that turn's delta — that is what the anchor means in the wire
contract — and absent means the body is the whole conversation. Two cases, no
search, no third case to adjudicate. The requirement now names the anchor
instead of asking an implementation to work the shape out.

## Why the zero-event precondition is the real fence

The claim budget stops *concurrent* duplicates. The zero-event check stops the
*wrong* duplicates. A single spooled response event proves upstream executed the
turn; at that point the outcome is not ambiguous, it is unknown-but-run, and
relocating would be a straightforward double-spend with no recovery value. The
ambiguity window exists for the same reason in the time dimension: a dispatch old
enough to be mid-execution is likelier to write its first event than to be lost.

## Relationship to #2336

`drop-bridge-recovery-modes` deleted a four-valued operator selector whose three
non-default values were at-least-once semantics nobody had enabled, and said
plainly that there is "no replacement, by design". This change restores one of
those semantics as default behaviour, which is a reversal of that conclusion, on
new information: the owner wants conversation survival, and the side-effect
dedupe contract that makes ② tolerable was already specified and is not
something #2336 considered.

Two things from #2336 are deliberately **not** reversed. The setting stays
deleted — this is one behaviour, not a mode selector, so
`[settings_fields].max` does not move. And the server-owned SSE recovery loop in
`app/modules/proxy/api.py`, with its keepalive stream and six-attempt cap, stays
deleted: relocation is a decision made once at the failover boundary, not a loop
that holds a client stream open while the server retries.

## Why five implementations collapse into one

The eligibility question is already answered independently in
`_service/streaming/retry.py`, twice in `_service/http_bridge/streaming.py`, in
`_service/compact.py`, and in `_service/websocket/helpers.py`. They do not agree
on scope, which is why the durable-transcript case is missing from all of them
rather than from one. The downstream fork `aafqaq/codex-lb-enhanced` implemented
the same feature by calling its projection directly from fourteen sites across
seven modules and then spent a day emitting roughly twenty consecutive
single-line corrections to those sites. The decision table in
`tests/unit/test_replay_relocation.py` is the regression wall against repeating
that: one function, one table, every transport delegating.

## Worked example

A thread has run five turns on account A. Account A hits its usage limit.

1. The client sends turn six: `previous_response_id = resp_A5`, one user message.
2. Account A answers HTTP 429 `usage_limit_reached`; no response event is
   emitted; nothing is downstream-visible. Evidence is **definitive**.
3. `get_replayable_transcript(resp_A5)` walks `resp_A5 -> resp_A4 -> ... ->
   resp_A1`, requiring a stored request body and a complete spool with a
   terminal event for each.
4. The rebuild concatenates each turn's request input with that turn's terminal
   output, then appends the client's new message. The anchor is dropped.
5. The strict account-neutral predicate runs on the result. It passes.
6. Account B serves turn six. The recovery budget is untouched.

If step 3 or step 5 fails, the client gets exactly what it gets today: the
owner-unavailable failure.

## Out of scope, and why

`Compact requests recover from quota-caused previous-response owner loss` holds
an explicit carve-out — "the durable prefix metadata that could prove it is
deliberately not consulted here". Reversing that is the natural follow-up, but
that requirement carries thirteen scenarios and a `MODIFIED` block must reproduce
all of them, which would double this change's size for a second concern. Compact
keeps today's behaviour until that follow-up.
