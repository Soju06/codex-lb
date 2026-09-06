## Context

#2089 bundled steering with async tools and configuration-update policy.
Maintainer required a split. rust-v0.153.4 does not emit `response.steer`.

## Goals / Non-Goals

**Goals:** owned-socket steering, one successor reservation for queued
steers, prepare-before-swap for explicit continuations.

**Non-Goals:** Ultra/`configuration_update` (#2097), async tool
continuity (#2099), catalog (#2085).

## Decisions

FOR UPDATE only on extend/reduce (`get_usage_reservation_for_update`),
then limit rows. Finalize/release stay on the existing unlocked read.

Do not globally fingerprint `upstream_payload["input"]`.

Release a steering placeholder only after
`_prepare_websocket_response_create_request` succeeds.

Preparation, owner resolution and admission can yield to the upstream reader.
Revalidate the exact control-map continuation and its pending, unassigned
placeholder under `pending_lock` before registering the replacement. Perform
the placeholder removal and continuation handoff in that same critical section.
If ownership changed, reject the explicit create with `response_not_found` via
the existing reservation/admission cleanup; do not revive a rejected steer or
replace a successor that already owns a response ID. For example, a final
`response.steer.failed` during admission must not leave an explicit create with
a steering parent but no matching control-map entry.

A replaced placeholder stays in a socket-owned release collection until its
release succeeds. Register this cleanup obligation inside the atomic swap,
before the release can yield or be cancelled. If release fails, continue the
explicit response and unrelated work; the existing tracked socket finalizer
retries that release once at teardown. A repeated failure is logged without
skipping the other socket cleanup. This is bounded cleanup, not a background
retry loop; a persistent database outage still requires stale-reservation
reclamation. For example, a transient failure releasing the old reservation
must leave it reserved until teardown refunds it, not settle it against the
explicit continuation usage.

Rejected: landing the full #2089 branch or introducing a new retry daemon for
this concrete post-swap ownership gap.

## Transport handoff for explicit continuation matching

Keep the existing attempt-start timestamp for request timeouts and retry
accounting. Track transport handoff separately for steering correlation. The
send scope supplies a synchronous, once-only callback; supported adapters
invoke it before exposing a response that can follow that send. The callback
becomes inert when its send scope ends, including cancellation or failure.

For websockets and aiohttp, observe successful writes on the connection's
owned transport. The observation belongs only to this send and transport,
including aiohttp's asynchronous compression task; unrelated writes must not
mark it dispatched. The notification occurs after the synchronous transport
write and before any post-write drain can yield. Do not equate adapter entry,
compression start, or send return with handoff. The archive wrapper preserves
the send scope without marking archive I/O as transport handoff.

For native egress, capture the callback with the exact outgoing command. The
WebSocket event pump invokes it when consuming the worker's successful send
acknowledgment, before delivering subsequent inbound frames. The existing
worker serializes socket send, acknowledgment, then receive; Python IPC write
or resuming the caller after its acknowledgment is not the correlation boundary.

Retain the documented priority of a dispatched explicit create over an
automatic same-parent successor. A local write is not proof of upstream causal
identity: both events currently carry the same parent ID. Stronger correlation
would require upstream metadata or an ordering guarantee outside this change.

Rejected alternatives: an attempt-start timestamp admits automatic responses
before compression/write; an until-send-return flag suppresses legitimate
explicit responses during drain. Rejecting all overlapping explicit creates
would remove the supported tool-output continuation and can deadlock a steer
waiting for required input. Handoff adds no new ordering restriction.

## Bounded correlation history

Retain at most 256 historical rejected-parent and suppressed-response IDs
before latching `retire_after_drain`. The limit is internal and needs no new
setting. Already admitted work may add its remaining correlation records;
new steers and unrelated creates are rejected while this generation drains.
The single downstream sender may finish admission of the frame it was already
handling when the limit was reached, subject to existing connection/ownership
revalidation; it cannot start an unbounded stream of new admissions.
Explicit required-tool-input creates for an existing continuation remain
admissible, including a replacement already registered before the latch.
Close the upstream only when the pending queue is empty, then reuse the
existing reader-done path to discard the control state and reconnect on demand.
For example, exhausting history while a steer awaits tool output must still
let that output reach the same upstream and settle before rotation.

Do not evict individual IDs on a live connection: a late automatic response
could otherwise consume an unrelated reservation. ID-less terminals cannot
identify which suppressed ID is safe to discard. Do not set
`reconnect_requested` before draining: the sender could wait for the reader
while the reader waits for that same unsent explicit request. Reject unrelated
creates during drain instead of waiting in the sender, which could block a
later required tool-input frame. Planned retirement neither fails pending
requests nor writes account-health penalties.

Check for an empty retiring queue in the reader loop as well as after relaying
text. During retirement, bound receive waits even when keepalives are disabled,
so a sender-side rejection can complete the drain without another upstream
event. This uses the existing receive timeout loop; it does not turn a drain
poll into a request timeout or an account-health failure.
