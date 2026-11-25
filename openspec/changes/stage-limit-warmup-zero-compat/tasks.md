## 1. Regression tests first

- [ ] 1.1 Add backend settings API coverage showing a direct zero update is accepted and returns/persists legacy `99.0`; run the focused test and confirm it fails against the current `gt=0` schema.
- [ ] 1.2 Add migration coverage showing the staged column is nullable, unbackfilled, and downgrade-safe; run the focused migration tests and confirm they fail before the revision exists.
- [ ] 1.3 Add frontend schema coverage showing response and update payloads accept zero while the existing form-bound behavior remains positive; run the focused Vitest file and confirm the zero cases fail before the schema change.

## 2. Compatibility implementation

- [ ] 2.1 Add the nullable inactive Alembic revision from the current main head and map the column in `DashboardSettings`; verify `make migration-check` and upgrade/downgrade tests.
- [ ] 2.2 Widen backend response/update bounds to include zero and normalize zero updates to the legacy `99.0` value at the repository boundary; verify the API regression test and existing settings tests.
- [ ] 2.3 Widen frontend response/update Zod bounds to include zero without changing the routing form's `min=1` or positive validation; verify the frontend schema and routing component tests.

## 3. Contract verification

- [ ] 3.1 Validate OpenSpec strictly and run focused backend/frontend tests, Ruff, type checks, and `git diff --check`.
- [ ] 3.2 Review the final diff against the proposal and confirm no runtime warm-up behavior, sentinel trigger, dual-write, or active-column backfill was introduced.
