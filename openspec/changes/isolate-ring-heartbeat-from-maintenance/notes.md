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

## Verification still blocked

- No local PostgreSQL service is available, so PostgreSQL-focused verification has not run.
- No verified production-sized database copy or approved Docker environment is available, so the candidate-image/database-copy soak in task 5.3 has not been attempted.
- No production database, container, compose stack, or service was touched.
