# Verification

Base: upstream `72630bbfe` (`main`). Branch: `feat/request-log-upstream-facet`.

- New backend tests against `main` code: 15 failed, as expected (no `upstream` param or facet).
- Same tests on this branch: `tests/integration/test_request_logs_upstream_filter.py` (9) and the
  8 new upstream tests in `tests/test_request_logs_options_api.py` pass; with the existing
  request-log API, list-count, rollup-parity and repository suites: **76 passed** (WSL Ubuntu,
  Python 3.13, SQLite).
- `make test-unit` slice (`tests/unit tests/simulation tests/test_request_logs_options_api.py`):
  10480 passed, 107 skipped, 1 xfailed, 1 failed. The failure,
  `tests/unit/test_db_sqlite_maintenance.py::test_runstate_clean_is_ignored_after_a_timestamp_preserving_restore`,
  is intermittent on `main` as well (1 of 5 isolated runs failed on `main`, 2 of 5 on the branch);
  this change does not touch `app/db` or that test.
- `ruff format --check`, `ruff check`, `ty check`, and the `make lint` architecture scripts: passed.
- Frontend `eslint`, `tsc -b`, and `vite build`: passed. Dashboard, reports and i18n suites:
  41 files, 511 tests passed. In the full `test:coverage` run, `src/__integration__/api-keys-flow.test.tsx`
  hit the 15 s test timeout on this Windows machine; the same timeout reproduces on `main`, and the
  branch does not touch the API-keys feature.
- `openspec validate add-request-log-upstream-facet --strict` and `openspec validate --specs`: passed.
- `check_simplicity_budgets.py`: README 9/10, `.env.example` 47/60, core nav 5/5. `git diff --check`: clean.
- Before/after screenshots (1440 px) captured with Playwright from the repo's screenshot fixtures plus
  three synthetic model-source rows, the same data for both builds. Images are in `evidence/`.

## Scope and limits

PostgreSQL was not run locally; `tests/test_request_logs_options_api.py` is in
`POSTGRES_PYTEST_TARGETS`, so CI runs the upstream skip-scan parity test there. The Rust and Helm
jobs of `make ci` were not run. The change stays active for upstream review.
