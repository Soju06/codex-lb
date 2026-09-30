## 1. Integration and scope

- [x] 1.1 Integrate main and preserve both streaming refresh callers; verify streamed usage-limit regressions.
- [x] 1.2 Remove branch-local convergence and restore topology/retirement artifacts; verify one head, both landed parent upgrades, downgrade/upgrade, and schema drift.
- [x] 1.3 Remove generic retained-invalidation changes and cross-namespace callbacks; verify cache-bus/upstream-route suites and explicit API-key publication regressions.

## 2. Authentication cost

- [x] 2.1 Add short per-entry TTL to the existing version-fenced cache; verify expiry and invalidation tests.
- [x] 2.2 Cache incomplete snapshots for at most five seconds; verify repeated product-path authentication, evidence recovery, revocation, and complete-estimate expiry tests.

## 3. Verification

- [x] 3.1 Run focused feature, migration, refresh, and invalidation tests plus lint/type/OpenSpec validation; record evidence and remaining policy holds.
- [x] 3.2 After an authorized commit, rerun the unchanged HEAD-to-disk migration graph assertion; verify it passes before archiving.

## 4. Reservation-failure review follow-up

- [x] 4.1 Reproduce missing prepared-state finalization through both public WebSocket routes, with initial/reused sockets and real fixed-limit/auth reservation refusals.
- [x] 4.2 Release and log the prepared state before rethrowing reservation domain errors; preserve success, error envelopes, transactional rollback, and healthy reused sockets.
- [x] 4.3 Synchronize admission spec/context, run regressions and static/spec checks, and obtain independent review before publication.
- [ ] 4.4 Verify exact-head GitHub review and CI after publishing the reservation fix and replying to the review thread; retain the maintainer policy hold.
