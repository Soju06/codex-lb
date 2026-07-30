## Context

The HTTP Responses bridge records the monotonic time of each actual
`response.create` send. If the request still owns the response-create gate and
has no `response.created`, matched `response.*` event, response id, downstream
sequence, or visible output, the upstream reader currently fails and retires
the session after `min(stuck_gate_threshold, 240 seconds)`.

The bridge already has a bounded `_retry_http_bridge_precreated_request` path.
It permits at most one replay, rejects ambiguous response progress, preserves
hard account ownership, protects account-scoped file references, and only
strips a continuation anchor when the proxy retained a fingerprint-safe full
resend body.

## Goals / Non-Goals

**Goals:**

- Recover the production eventless failure before the client-safe timeout.
- Reuse the existing replay and ownership rules instead of creating another
  retry policy.
- Settle and retire exactly once when recovery is not safe or does not work.
- Keep missing acknowledgement neutral to account health.

**Non-Goals:**

- Recover streams that have matched any `response.*` lifecycle event.
- Add durable cooldown or poison state across requests or replicas.
- Retry more than once, extend the original request budget, or change public
  response framing.

## Decisions

### 1. Use a 30-second acknowledgement window

The eventless watchdog uses
`min(http_responses_session_bridge_stuck_gate_retire_after_seconds, 30
seconds)`, measured from the current send. Normal production TTFT is generally
sub-second to low-single-digit seconds; 30 seconds leaves margin for transient
startup delay while removing the four-minute dead period. Each real resend
replaces the timestamp, so a replay gets one fresh acknowledgement window
without extending the original request budget.

### 2. Replay through the existing pre-created helper

After eligibility is rechecked under lifecycle and pending-state locks, the
reader cancels the old socket receive task and invokes the existing pre-created
replay helper only when the eventless owner is the session's sole pending
request. A successful reconnect/resend returns control to the same reader loop,
which waits on the replacement socket while the downstream request stays open.

Cancellation is conditional on the receive task still being pending. If
`response.created` or another upstream result wins that race, the reader leaves
the completed task intact and processes it through the normal event path
instead of replaying.

The helper's existing `replay_count` bound makes this a single recovery
attempt. This timeout recovery reconnects on the current account because the
request still holds that account's response-create concurrency lease.
Continuations are replayed only from an explicitly retained retry-safe
full-resend body, and file ownership continues to require the preferred
account.

### 3. Retire only after recovery is unavailable or exhausted

If the helper declines replay, reconnect/resend fails, or the replacement send
also reaches the deadline, the reader applies the existing
`missing_response_created_timeout` overrides, records the stuck-retirement
metric and terminal log, settles pending requests, and retires the bridge.
Neither the retry nor terminal path marks the account unhealthy solely because
the acknowledgement was missing.

## Failure Modes

- **A warm socket is already closed before the next send begins.** The transport
  adapter returns a sealed not-dispatched proof without calling its send
  primitive. The HTTP bridge or direct WebSocket proxy reconnects once on the
  same leased account and sends the exact request, including a continuation
  anchor, because upstream could not have accepted the first attempt.
- **The socket closes during send.** Dispatch is ambiguous, so the existing
  fail-closed 502 remains authoritative and no internal resend occurs.
- **The original send was accepted but its acknowledgement was lost.** Closing
  the old socket discards any later output. Because no response lifecycle or
  downstream-visible output was observed, client-side tools or other
  downstream effects have not run; the bounded replay may spend extra upstream
  compute but does not duplicate downstream effects.
- **The acknowledgement completes while cancellation begins.** The completed
  receive remains authoritative and is processed normally; no replay occurs.
- **The relay is cancelled while awaiting child cancellation.** The relay's
  own cancellation remains authoritative and propagates; a closed session
  cannot reconnect or resend after ownership and leases are released.
- **The request is continuity- or file-bound without safe replay evidence.**
  The existing helper declines replay and the request fails closed at 30
  seconds.
- **Another request is pending on the same socket.** Reconnecting could orphan
  that sibling's response, so the proxy skips replay and retains the existing
  whole-session terminal cleanup.
- **The reconnect or resend fails.** Existing typed retry errors are preserved
  and the bridge is settled and retired exactly once.
- **The replacement socket also stays silent.** `replay_count` blocks a second
  replay; terminal cleanup runs after the replacement's 30-second window.

## Example

A request sends at monotonic time 1,000 and receives no matched response event.
At 1,030 the reader cancels the old receive and safely resends once on a fresh
same-account socket. If the original receive completes during cancellation,
that result is processed and no replay occurs. If `response.created` arrives
from the replacement at 1,032, the original downstream stream continues
normally. If the fresh socket is still eventless at 1,060, the proxy returns
the existing explicit timeout and retires the bridge.

A later compacted continuation finds its warm socket already closed. The
adapter rejects the frame before invoking the socket send operation, the bridge
reconnects once to the same account, and the original downstream stream
continues without a client-visible reconnect. If the socket instead fails while
the send operation is in progress, the bridge does not replay.
