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
