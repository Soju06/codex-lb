## Integration baseline

The reviewed implementation was checkpointed at `da5baa0232af1c56ee96c8ba421d391e0c67732b` with backup ref `backup/isolate-ring-heartbeat-before-main-rebase-20260905`, then rebased onto `origin/main@0a726558a1b9994d4943c9c8cff295b267d879f0`.

Reconciliation retained current-main shutdown cleanliness accounting, native-egress cleanup, bounded spool startup cleanup, and stale-operation abandonment. The heartbeat change reuses `_await_task_deferring_cancellation()` from `app/core/utils/shared_future.py`; it does not restore the superseded cancellation module. Heartbeat registration, renewal, and stale-marking use the existing background database pool while cap and durable maintenance retain the request pool. Stale-operation abandonment runs in the bounded `durable_ownership` owner, is attempted even when ownership reconciliation fails, and propagates protection/query failures into phase telemetry. An unsettled periodic owner now suppresses the SQLite clean marker. The existing lifespan shutdown regression stubs the account-deletion scheduler so independently owned cancellation-safe shutdown is not pinned by an unrelated real database query.

## Post-rebase verification evidence

Passing:

- 110 focused periodic-owner, ring lifecycle, health, metrics, SQLite ring-membership, and cap-partition tests.
- 79 file-backed SQLite durable bridge-ring lifecycle tests.
- 77 database-session tests.
- 9 focused HTTP bridge maintenance/reconciliation tests.
- All 51 `tests/unit/test_otel.py` lifespan and shutdown tests.
- 5 health integration/E2E tests.
- 16 of 17 `tests/integration/test_health_and_errors.py` tests; the sole failure is the unchanged missing-built-dashboard-assets case below.
- 980 of 981 `tests/unit/test_proxy_http_bridge.py` tests; the sole failure is the unchanged missing `file_account_pins` fixture case below.
- Repository Ruff lint and formatting (`1025 files already formatted`).
- Repository `uv run ty check`.
- Cancellation-safety architecture check.
- Strict validation for `isolate-ring-heartbeat-from-maintenance`.
- Repository-wide OpenSpec validation: 58 passed, 0 failed.
- Pi review session `f017948d-2a42-4305-bbe3-06f6aa2e818e`; four concrete findings were fixed and the final re-review reported no actionable issues.

Current-main baseline failures reproduced or confirmed separately:

- `scripts/check_proxy_architecture.py` reports `service.py has 2601 lines; limit is 2600` on both this branch and unmodified `origin/main@0a726558`.
- `tests/integration/test_health_and_errors.py::test_assets_js_served_as_javascript_despite_poisoned_registry` requires a frontend build and fails with `built dashboard assets missing`.
- `tests/unit/test_proxy_http_bridge.py::test_stream_via_http_bridge_fails_closed_before_file_affinity_when_previous_response_owner_misses` reaches a real SQLite file-pin repository without the `file_account_pins` fixture table.

The six historical `tests/unit/test_otel.py` database-mocking failures are resolved on current main; they are no longer listed as baselines.

## Verification still blocked

- No local PostgreSQL service is available, so PostgreSQL-focused verification has not run.
- No verified production-sized database copy or approved Docker environment is available, so the candidate-image/database-copy soak in task 5.3 has not been attempted.
- No production database, container, compose stack, or service was touched.
