## 1. Implementation

- [x] 1.1 Record a settlement claim under the pending lock when terminal-event
      processing pops requests from pending ownership.
- [x] 1.2 Settle claimed-but-unfinalized requests under a shielded scope when
      the bookkeeping continuation raises or is cancelled, including the
      grouped previous-response error path, and unblock the downstream waiter.
- [x] 1.3 Let request detachment reclaim settlement for an abandoned claim
      instead of keying solely on pending-deque membership.
- [x] 1.4 Enforce a hard reservation age ceiling in stale usage-reservation
      reclamation regardless of heartbeat `updated_at` refreshes.

## 2. Regression coverage

- [x] 2.1 Abort completed bookkeeping after the pending pop with an exception
      and with a cancellation; assert the heartbeat finished, the reservation
      was released once, and no touch runs afterward.
- [x] 2.2 Abort the grouped previous-response finalize loop mid-way; assert
      the popped remainder is settled.
- [x] 2.3 Assert detach reclaims an abandoned claim and leaves a live claim to
      its bookkeeping owner.
- [x] 2.4 Assert the janitor reclaims a reservation older than the hard
      ceiling despite a fresh heartbeat `updated_at`, and that the scheduler
      passes the ceiling.
- [x] 2.5 Verify the regressions fail on the pre-fix implementation.

## 3. Validation

- [x] 3.1 Run the HTTP bridge unit suites, API key scheduler/janitor tests,
      Ruff checks, ty, and architecture checks.
- [x] 3.2 Run strict OpenSpec validation.
