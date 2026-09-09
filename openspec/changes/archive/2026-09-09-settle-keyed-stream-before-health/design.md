## Context

The keyed HTTP SSE stream holds a shared `_StreamSettlement` until terminal
usage is known. Mid-loop penalties already use
`_settle_stream_usage_before_pending_penalty`, and downstream-close terminal
work already uses `_finalize_terminal_settlement_after_downstream_close`.
Three owner-unavailable rewrite branches in the stream mixin bypass that order,
and the retry loop's empty-queue terminal branch writes health/success before
its `finally` can release the reservation.

## Goals / Non-Goals

**Goals:**

- Make confirmed settlement or fail-safe release the gate for health writes.
- Preserve the original upstream recovery code for health classification while
  retaining the sanitized client/log error code.
- Reuse the existing settlement tracker and helpers without another coordinator
  or ownership type.
- Prove first-event, later-event, raised-error, empty-queue, and unconfirmed
  cleanup behavior deterministically.

**Non-Goals:**

- Missing-usage policy, unary routes, WebSocket/compact/precreated bridge paths,
  or source-ownership/stale-anchor matching changes.
- Client-visible error-envelope changes.

## Decisions

1. Owner-unavailable rewrite branches retain the original error code, set an
   explicit ordering marker on the existing settlement state, and settle
   through `_settle_stream_usage_before_pending_penalty` before invoking health.
   Reusing the existing helper preserves fallback-release and transfer
   semantics; an inline release would create a second cleanup owner.
2. The empty-queue terminal branch passes explicit ordering ownership to
   `_finalize_terminal_settlement_after_downstream_close` before terminal
   health/success. This keeps one terminal path responsible for usage and
   cleanup, including successful terminals, without making unrelated stream
   errors wait synchronously.
3. Health is conditional on a confirmed helper result. The client and request
   log continue to use `previous_response_owner_unavailable`; only account
   recovery receives the original upstream code.
4. Tests use an order ledger and event-controlled fakes. No timing waits or
   sleeps are needed.

## Risks / Trade-offs

- [Risk] Ordering-sensitive error paths wait for persistence before health. ->
  Mitigation: this is the required correctness exception already used by keyed
  mid-loop and WebSocket settlement.
- [Risk] Overlapping PRs alter the same files. -> Mitigation: preserve their
  independent stale-anchor shape and source-ownership behavior and document the
  overlap for maintainers.
