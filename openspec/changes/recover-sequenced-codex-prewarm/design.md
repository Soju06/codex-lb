## Context

The direct Responses WebSocket path supports a bounded one-shot replay before
downstream-visible output. The sequence-safety contract added for #1222
disables that replay after any numeric `sequence_number` has been sent because
a fresh upstream generation normally restarts its sequence and can duplicate
semantic events under the original downstream response id.

Codex `generate = false` prewarms are a narrower protocol shape. They warm and
establish request continuity but do not generate model output or tool calls. A
normal successful prewarm emits `response.created` at sequence `0`, followed by
an empty `response.completed`. If the upstream socket disappears between those
events, the general sequence guard currently turns a recoverable no-output
failure into a client-visible reconnect.

## Goals / Non-Goals

**Goals:**

- Recover an accepted, created-only Codex `generate = false` prewarm through
  the existing one-shot reconnect and replay path.
- Preserve a strictly advancing downstream numeric sequence.
- Keep request ownership, response-create admission, reservations, logging,
  and Lite continuity finalized exactly once.
- Preserve the general fail-closed rule for every model-generating or
  semantically progressed request.

**Non-Goals:**

- Renumber, offset, or synthesize sequence numbers.
- Replay normal model turns after a numeric sequence is visible.
- Retry more than once, replay multiplexed pending requests, or change HTTP
  bridge behavior.
- Treat the client metadata label alone as proof that a request is a prewarm.

## Decisions

### Verify the no-generation request shape

Replay eligibility requires both trusted request observations:

- Codex turn metadata classifies the request as `request_kind = "prewarm"`.
- The normalized request body contains the literal boolean `generate = false`.

The request state records this conjunction at preparation time. A caller that
only spoofs the metadata label while sending a generating request receives no
exception to the sequence guard.

### Limit recovery to the initial created watermark

The exception applies only when the request has:

- exactly one recorded `response.*` progress event (`response.created`),
- an assigned response id and no longer awaits `response.created`,
- no text or tool output marked visible,
- a downstream numeric watermark of exactly `0`, and
- no previous replay attempt.

Direct-WebSocket response progress is recorded when the event is matched,
including non-terminal events. This prevents an intervening
`response.in_progress` or other response event from being mistaken for a
created-only state.

Requiring watermark `0` matches the native prewarm start sequence and avoids
sequence offsets. Any other exposed numeric watermark retains the existing
1011 fail-closed behavior.

### Preserve, rather than rewrite, sequence ordering

The replayed `response.created` remains suppressed and its upstream response
id remains rewritten to the original downstream-visible id, as in the existing
created-only replay path. The original watermark remains `0`.

Before a later replay event is finalized or forwarded, any numeric sequence
that is less than or equal to the exposed watermark is treated as a replay
sequence violation. The proxy settles the request as `stream_incomplete`, emits
no synthetic terminal frame under the sequenced response id, and closes the
downstream WebSocket with code 1011. No sequence is renumbered or invented.

### Keep the retry and ownership bounds unchanged

The existing `replay_count < 1`, single-pending-request, replay-safe anchor,
file-owner, account-selection, admission, and reservation rules continue to
apply. The exception only relaxes the numeric-watermark predicate for the
verified prewarm state; it does not create a second retry mechanism.

## Risks / Trade-offs

- A prewarm may be accepted twice upstream. Because `generate = false` cannot
  produce model output or external tool side effects, the cost is limited to
  duplicate cache warming and is preferable to failing the client turn.
- The exception intentionally does not recover non-zero initial watermarks or
  prewarms with extra progress. Those cases remain client-visible reconnects
  because preserving their semantics would require sequence rewriting or
  broader event deduplication.
- Incorrect response progress accounting could broaden replay eligibility, so
  unit and endpoint regressions cover created-only, extra-progress, spoofed
  metadata, ordinary-turn, and non-advancing-replay cases.
