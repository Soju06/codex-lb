## 1. Native WebSocket capacity

- [x] 1.1 Implement shared byte accounting, fair reader/pump scheduling and cleanup; verify burst, overflow isolation, acknowledgement and release tests.
- [x] 1.2 Make local consumer backpressure account-neutral; verify adapter and externally failing relay settlement/health paths.

## 2. HA capacity and rollout

- [x] 2.1 Configure three 3-GiB base backends plus surge, 1-GiB buffer budgets and shared DB pool limits; verify rendered Compose and resource arithmetic tests.
- [x] 2.2 Generalize rollout, migration, recovery and rollback for amber; verify fake-Docker ordering, failures and eligible-count invariants.
- [x] 2.3 Apply leastconn through validated graceful proxy reload; verify failed reload is fail-closed and locally exercise master-worker reload without production mutation.

## 3. Documentation and verification

- [x] 3.1 Sync owning specs/context and published deployment docs; update and validate deployment skill, keeping authorization and drain limits explicit.
- [x] 3.2 Run mocked concurrent 100/300/500-session burst checks and regression/lint/type/spec validation; record measured scope and limitations.
- [x] 3.3 Verify implementation against the change; record production deployment as pending separate authorization and hand off the verified artifacts for archive.
