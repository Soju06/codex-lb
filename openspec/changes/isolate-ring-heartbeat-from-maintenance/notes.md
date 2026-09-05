## Implementation baseline

Implementation is currently stacked on reviewed cancellation-fix head `825ab217c4fd1cd3889f53b6f7ac7399547c5b44` because PR #1958 and the selected spool-cleanup lifecycle change have not merged. The final branch still requires task 1.1: rebase onto current `main`, inspect `app/main.py` and shutdown-test conflicts, and rerun validation.

## Verification evidence

Passing on the stacked worktree:

- 105 focused periodic-owner, ring lifecycle, health, metrics, SQLite ring-membership, and cap-partition tests.
- 57 file-backed SQLite durable bridge-ring lifecycle tests.
- 6 focused existing HTTP bridge maintenance/reconciliation tests.
- 15 health integration/E2E tests; the remaining selected test failed only because built dashboard JavaScript assets are absent from this worktree.
- Repository Ruff lint and formatting (`970 files already formatted`).
- Repository `ty check`.
- Proxy architecture and cancellation-safety checks.
- Strict validation for `isolate-ring-heartbeat-from-maintenance`.
- Required Pi review session `ebd3c19f-1f5f-467c-9a30-cb172b030d0f`; final re-review found no concrete remaining issues.

Known unrelated baseline failures:

- `tests/unit/test_otel.py` has six lifespan tests that fail because those tests mock `init_db()` but use the real `SessionLocal`, then query missing `accounts`, `runtime_sentinels`, and `cache_invalidation` tables. The same six failures reproduce unchanged on dependency head `825ab217` without this change.
- Repository-wide OpenSpec validation is 57/58 because `openspec/specs/model-source-routing/spec.md` lacks `## Purpose`.
- `tests/integration/test_health_and_errors.py::test_assets_js_served_as_javascript_despite_poisoned_registry` requires a built frontend and failed with its existing “built dashboard assets missing” assertion.

## Verification still blocked

- No local PostgreSQL or Docker daemon is available, so PostgreSQL-focused verification has not run.
- No verified production database copy is available, so a candidate image and the database-copy soak in task 5.3 have not been attempted. No production database or service was touched.
