## 1. Integration Baseline and Periodic Owner Contract

- [x] 1.1 After `prevent-level-cancellation-busy-spin` and any selected spool-cleanup lifecycle change land, rebase this branch on current `main`, inspect every shared-file change in `app/main.py` and shutdown tests, and verify the OpenSpec change still passes strict validation.
- [x] 1.2 Add deterministic unit coverage for a periodic owner whose phase succeeds, fails, exceeds its deadline, finishes late, and exits unexpectedly; verify tests prove monotonic cadence, one in-flight child, consumed exceptions, bounded restart delay, and no catch-up burst.
- [x] 1.3 Implement the shared periodic phase owner/supervisor with fixed internal heartbeat and maintenance deadlines, strong task references, low-cardinality outcome hooks, and cancellation-safe shutdown; verify the periodic-owner tests pass.

## 2. Independent Ring and Maintenance Lifecycles

- [x] 2.1 Extract bridge registration from the current serial heartbeat loop and start separately supervised ring-heartbeat, durable-ownership, idle-sweep, and cap-partition owners only after registration succeeds; verify registration retry and startup readiness tests still pass.
- [x] 2.2 Make heartbeat success, failure, timeout, late completion, and recovery flow through the supervised owner without overlapping heartbeat attempts; verify a blocked attempt ages the row out while a later successful upsert restores membership.
- [x] 2.3 Move durable-ownership reconciliation (including stale-operation abandonment) and idle-session sweeping to separate bounded owners while preserving existing eligibility, fencing, and close behavior; verify a blocked or failed reconciliation neither delays heartbeat nor skips stale-operation cleanup or idle sweeping.
- [x] 2.4 Move cap-partition refresh to its own bounded owner while preserving initial refresh, self-counting, last-known partition fallback, and hysteresis; verify blocked refresh does not delay heartbeat and is not invoked concurrently.
- [x] 2.5 Update lifespan shutdown to attempt bounded drainage of every supervisor and phase child, requiring registration and heartbeat to stop before `mark_stale()` even if maintenance remains active; make periodic drainage and stale-marking consume one shared absolute process-shutdown deadline; hard-bound stale-mark observation while retaining strong ownership of late cleanup; verify no periodic or stale-mark task remains untracked, no renewal occurs after stale-marking, and an unsettled owner suppresses the SQLite clean-shutdown marker.

## 3. Readiness and Heartbeat-Age Reporting

- [x] 3.1 Extend the bridge-ring health query and schema with nullable, nonnegative `heartbeat_age_seconds` derived from the probed replica's own row while keeping fingerprint and ring size limited to fresh members; verify fresh, stale, missing, future-skewed, and lookup-error cases.
- [x] 3.2 Preserve the post-registration empty-ring readiness exemption per maintainer review; specify and verify that bridge-disabled readiness remains HTTP 200 after a successful database probe regardless of bridge state; verify stale/missing single-member diagnostics, nonempty-ring exclusion, registration-incomplete precedence, and liveness.
- [x] 3.3 Add the heartbeat last-success timestamp gauge, heartbeat failure counter, and bounded phase/outcome maintenance counter with multiprocess-safe aggregation; verify metric names, allowed labels, increments, and Prometheus-unavailable no-op behavior.
- [x] 3.4 Add structured diagnostics for heartbeat failure/recovery, unexpected supervisor exit, maintenance failure/timeout, and late completion without sensitive or high-cardinality fields; verify logs contain phase, outcome, elapsed/age, and consecutive-failure data where applicable.

## 4. Product-Surface and Partial-Failure Regressions

- [x] 4.1 Add lifecycle regressions that block each optional phase in turn for longer than the heartbeat interval and prove heartbeat callbacks continue advancing without overlap, plus a product-path lifespan regression that verifies the persisted `BridgeRingMember.last_heartbeat_at` advances while real maintenance wiring is blocked.
- [x] 4.2 Add bridge-owner and cap-partition partial-failure regressions proving each phase owns a distinct database session/task, heartbeat pool admission remains available when the production-sized request pool is occupied, unfinished work is not duplicated, and late completion is settled exactly once.
- [x] 4.3 Add shutdown regressions for cancellation during heartbeat, maintenance, and registration, verifying bounded drainage, task deregistration, and stale-row ordering.

## 5. Verification and Rollout Evidence

- [ ] 5.1 Run focused health, ring-membership, bridge lifecycle, cap-partition, shutdown, SQLite, and PostgreSQL suites and record passing results plus any proven unrelated baseline failures.
- [x] 5.2 Run repository formatting, lint, type, architecture, and strict change-local OpenSpec validation; run repository-wide OpenSpec validation and record any pre-existing failure separately.
- [ ] 5.3 Build a candidate image and exercise blocked-maintenance, heartbeat failure/recovery, and shutdown-during-overdue-phase scenarios against a verified database copy; verify heartbeat age, readiness, ring size, maintenance metrics, task counts, and event-loop lag before recommending production rollout.
