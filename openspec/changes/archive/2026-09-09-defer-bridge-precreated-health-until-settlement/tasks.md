## 1. Defer keyed pre-created health writes

- [x] 1.1 Route the three pre-created retry arms through a defer-or-handle
      helper that queues keyed penalties on the request state and keeps the
      immediate write for unkeyed requests
- [x] 1.2 Drain the queue after committed settlement in the websocket
      finalizer and after fallback release in the reservation release helper,
      on a lane independent of the deferred account-backoff drain
- [x] 1.3 Run each drain attempt as an owned shielded task so caller
      cancellation consumes the entry exactly once, and log-and-drop an
      attempt that fails after settlement committed

## 2. Regression coverage

- [x] 2.1 Keyed capacity and keyed usage-limit pre-created failures through
      the real bridge event processor: no health write before settlement,
      penalty queued, retry flow preserved
- [x] 2.2 Settle-then-health order on release; unconfirmed release keeps the
      penalty unapplied and retained
- [x] 2.3 Backoff-lane failure still drains the health lane; cancellation
      consumes the entry exactly once; a failed health write is dropped
      without aborting finalization
