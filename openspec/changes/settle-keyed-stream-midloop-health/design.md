## Context

Keyed streaming Responses requests hold one API-key reservation across mid-loop
account failover. Immediate `_handle_stream_error` / `record_errors` on those
failover continues would write health while the reservation is still open.
Deferred penalties flush after settlement, matching compact's settle-then-health
order.

## Goals / Non-Goals

**Goals**

- Keep one reservation across mid-loop keyed failover.
- Flush deferred account-health only after confirmed settlement (or confirmed
  fail-safe release when ordered settle never ran).
- Make settlement visibility durable before cancellable health awaits so cancel
  during flush still drains retained penalties.
- Prove the contract through `_stream_responses`, not only private helpers.

**Non-Goals**

- Changing websocket terminal settle-before-health beyond existing api-keys
  requirements.
- Changing non-keyed stream health timing.

## Decisions

1. Queue mid-loop keyed health into `pending_post_refresh_transient_penalties`
   and flush from `_settle_stream_usage_before_pending_penalty`.
2. Assign `settled = True` immediately after successful settle, before drain /
   flush awaits.
3. When cancel interrupts a post-settle flush, finish remaining queued penalties
   from cleanup; use cancel-safe scheduling when the task is already cancelling.
4. Pop each queued entry only after that entry's health write attempt finishes
   (success or logged failure); let `CancelledError` keep later entries queued.
5. Keep the settlement tracker responsible for retrying release when an
   ordering-sensitive settlement and its immediate fallback both fail after
   ownership transfer.
6. Name cancel-safe deferred-health flushes as persistence work so
   `drain_persistence_tasks` waits for them during graceful shutdown.

## Risks / Trade-offs

- Cancel-safe background flush and retrying release can outlive the request
  task; tracked cleanup tasks must remain drained by shutdown ownership.
- Unconfirmed settlement MUST withhold deferred health (already required for
  retry settlement).
