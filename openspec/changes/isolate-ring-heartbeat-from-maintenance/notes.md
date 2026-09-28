## PR #2133 2026-09-14 maintainer follow-up

- Rebased onto current `origin/main@d1fd2f21fa0e0f3b5fcad3af5fada19693cd1fc1`. The only conflict was the metric-registration test: the resolution preserved current main's removal of the reverted subscription-overflow checks and reapplied only this change's heartbeat metric assertions.
- Every `/health/ready` HTTP 503 path now constructs the same validated unavailable envelope (`status`, nullable `checks`, nullable `bridge_ring`, and `detail`), and the route documents that model. Success responses are unchanged.
- Health-side ring fingerprinting sorts active member ids in Python as well as requesting database order, preserving one cross-engine codepoint-order contract.
- Design now states that a phase deadline is diagnostic: a permanently wedged idle sweep parks that owner rather than being cancelled or duplicated, while the heartbeat and other owners continue.
- SQLite uses a single writer. The background pool isolates heartbeat connection admission, not file-lock acquisition; bounded singleflight owners and the short heartbeat upsert limit contention, while the heartbeat timeout/retry path handles a lock held past the diagnostic bound.

## Production database-copy and contention evidence

- A beta.7 derivative carrying the heartbeat implementation from PR head `16c12122` was built for the production arm64 platform and matched the reviewed application hashes. A representative 48,394,301,440-byte production copy passed pre/post migration integrity, foreign-key, revision, and schema-policy checks; its blocked-maintenance/failure-recovery soak kept heartbeats advancing without overlap and shut down cleanly.
- The controlled production rollout compacted the live SQLite database from 48,556,052,480 bytes to approximately 1.19 GB while retaining the stopped original as rollback. Migration, rollup finalization, full destination integrity, foreign-key checks, revision checks, and retention-policy checks passed.
- During the arm64 and production-copy fault soaks, one injected heartbeat failure and one maintenance timeout were observed and recovered without overlap; SQLite CLEAN remained suppressed while required work was unsettled.
- After roughly 45 hours of production traffic, the container had zero restarts, the persisted heartbeat was advancing, local/public readiness was healthy, and the preceding hour contained no SQLite-lock, disk-full, heartbeat-failure, maintenance-timeout, backpressure, queue-full, traceback, OOM, or HTTP 502 signatures. The last 24-hour traffic window was 4,038 successes out of 4,068 requests; the remaining outcomes were client cancellations or classified upstream failures.
- The PR's GitHub PostgreSQL job passed at head `16c12122`. The production deployment remains single-instance SQLite; no claim of zero-downtime migration is made.
- Post-rebase local follow-up validation passed 119 periodic/ring/health/metrics/cap tests, 69 health/degradation/ring/metrics tests, 11 integration/E2E health tests, 10 selected HTTP bridge maintenance tests, and 207 database/shutdown/lifespan tests with the one reproduced current-main baseline deselected. Repository lint, formatting, type checking, strict change validation, and all 65 repository specs passed.

## PR #2133 maintenance-only shutdown regression

- The lifespan shutdown matrix now exercises the production stale-mark/CLEAN gates with fully drained owners, an active heartbeat writer, and maintenance-only incomplete drainage. Real test owners are drained before injecting the partial result, avoiding leaked test tasks.
- Maintenance-only drainage attempts `mark_stale()` but withholds SQLite CLEAN; an active heartbeat writer prevents stale-marking. Both cases also cover database-disposal failure.
- Validation: 52 lifespan tests passed with the previously documented current-main baseline deselected; focused Ruff, formatting, and type checks passed. No production environment was mutated.

## PR #2133 current-head review follow-up

- Periodic-owner drainage and `mark_stale()` now consume one absolute deadline derived from the remaining process-shutdown budget. Their existing per-step caps remain, but they cannot add together past that deadline.
- The bridge-ring delta spec now explicitly requires bridge-disabled `/health/ready` to return HTTP 200 after a successful database probe regardless of bridge schema, registration, lookup-error, or membership state; the health regression exercises those conditions.
- A product-path lifespan regression now uses the real ring service and separate SQLite request/background pools, blocks the wired durable-maintenance callback past its deadline, exhausts the request pool, and verifies the persisted `BridgeRingMember.last_heartbeat_at` advances without overlapping maintenance work.
- Rebased without conflicts onto `origin/main@82567556f9f75ea13986667fc5282f035b7ca8d2`; `git range-diff` reports the first seven feature commits patch-equivalent to the reviewed pre-rebase stack, while the eighth differs only by this post-rebase verification note.
- Current follow-up validation after that rebase: 120 periodic/ring/health/metrics/cap tests, 85 file-backed bridge lifecycle tests, 86 database-session tests, 69 graceful-shutdown/drain-bound tests, 50 lifespan tests with the one current-main baseline failure deselected, and 10 selected HTTP bridge maintenance tests passed in isolated SQLite databases. Repository Ruff/formatting (`1287 files already formatted`), `ty`, proxy architecture, cancellation-safety, proxy timing-seam, strict change validation, and all 65 repository specs passed.
- No production environment, database, container, or deployment was mutated while addressing these comments.

## Earlier PR #2133 review follow-up

- Stale-marking now depends only on registration and heartbeat stopping; an active maintenance owner still suppresses the SQLite clean marker but cannot prevent deliberate ring expiry after the bounded drain.
- Kept the existing empty-ring readiness exemption. A stricter single-replica policy is deferred, not authorized by this PR; heartbeat-age diagnostics remain.
- Registration and periodic shutdown share one monotonic deadline, without an additive 100ms grace. Periodic cancellation begins without waiting for registration to settle.
- Fixed the gauge-observation race, added metric-label and idle-pass isolation assertions, clarified the health schema change, and moved architecture rationale out of the normative spec.
- Validation: 115 lifecycle/readiness/lifespan/periodic tests and 14 selected metrics/maintenance tests passed; full lint (including architecture, cancellation, and timing guards), type checking, strict change validation and all 58 repository specs passed.
- No production environment, database, container, or deployment was mutated while addressing these comments.

## Integration baseline

The reviewed implementation was checkpointed at `da5baa0232af1c56ee96c8ba421d391e0c67732b` with backup ref `backup/isolate-ring-heartbeat-before-main-rebase-20260905`, then rebased onto `origin/main@0a726558a1b9994d4943c9c8cff295b267d879f0`.

Reconciliation retained current-main shutdown cleanliness accounting, native-egress cleanup, bounded spool startup cleanup, and stale-operation abandonment. The heartbeat change reuses `_await_task_deferring_cancellation()` from `app/core/utils/shared_future.py`; it does not restore the superseded cancellation module. Heartbeat registration, renewal, and stale-marking use the existing background database pool while cap and durable maintenance retain the request pool. Stale-operation abandonment runs in the bounded `durable_ownership` owner, is attempted even when ownership reconciliation fails, and propagates protection/query failures into phase telemetry. An unsettled periodic owner now suppresses the SQLite clean marker. The existing lifespan shutdown regression stubs the account-deletion scheduler so independently owned cancellation-safe shutdown is not pinned by an unrelated real database query.

## Post-rebase verification evidence

Passing:

- 110 focused periodic-owner, ring lifecycle, health, metrics, SQLite ring-membership, and cap-partition tests.
- 79 file-backed SQLite durable bridge-ring lifecycle tests.
- 77 database-session tests.
- 9 focused HTTP bridge maintenance/reconciliation tests.
- 50 `tests/unit/test_otel.py` lifespan and shutdown tests passed after the later current-main baseline failure below was deselected.
- 5 health integration/E2E tests.
- 16 of 17 `tests/integration/test_health_and_errors.py` tests; the sole failure is the unchanged missing-built-dashboard-assets case below.
- 980 of 981 `tests/unit/test_proxy_http_bridge.py` tests; the sole failure is the unchanged missing `file_account_pins` fixture case below.
- Repository Ruff lint and formatting (`1272 files already formatted`).
- Repository `uv run ty check`.
- Cancellation-safety architecture check.
- Strict validation for `isolate-ring-heartbeat-from-maintenance`.
- Repository-wide OpenSpec validation: 65 passed, 0 failed.
- Pi review session `f017948d-2a42-4305-bbe3-06f6aa2e818e`; four concrete findings were fixed and the final re-review reported no actionable issues.

Current-main baseline failures reproduced or confirmed separately:

- `scripts/check_proxy_architecture.py` reports `service.py has 2601 lines; limit is 2600` on both this branch and unmodified `origin/main@0a726558`.
- `tests/integration/test_health_and_errors.py::test_assets_js_served_as_javascript_despite_poisoned_registry` requires a frontend build and fails with `built dashboard assets missing`.
- `tests/unit/test_proxy_http_bridge.py::test_stream_via_http_bridge_fails_closed_before_file_affinity_when_previous_response_owner_misses` reaches a real SQLite file-pin repository without the `file_account_pins` fixture table.

The six historical `tests/unit/test_otel.py` database-mocking failures are resolved on current main. The later `tests/unit/test_otel.py::test_lifespan_drains_actual_audit_and_cancelled_fleet_tasks_before_resource_close` failure reproduces on unmodified current main and is the only lifespan baseline excluded from the current follow-up run.

## Resolved rollout gates

- PostgreSQL-focused coverage passed in PR CI at head `16c12122`; the local environment remains SQLite-only.
- Candidate-image, representative database-copy, arm64 fault-soak, and controlled production rollout evidence is recorded above. The fresh stopped pre-upgrade database, compose file, and prior image remain available for rollback.
- Production deployment is operational evidence, not permission to self-merge PR #2133; current-head CI, review threads, and mergeability still govern the PR.
