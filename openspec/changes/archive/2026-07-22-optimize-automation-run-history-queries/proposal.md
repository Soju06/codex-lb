## Why

Grouped automation run history repeatedly aggregates the full matching history before pagination, then repeats substantially the same work for totals and filter options. At 10,000 cycles / 80,000 runs this costs hundreds of milliseconds per poll on both SQLite and PostgreSQL even though the common unfiltered page does not need dynamic cycle-status computation.

## What Changes

- Use a lightweight grouped-history query when no effective-status filter is requested, while preserving cycle selection, representative-run, ordering, and pagination semantics.
- Keep the full dynamic eligibility/status calculation for status-filtered requests, but centralize it so page and options queries cannot diverge.
- Return page rows and their exact total from one database snapshot; retain a bounded count fallback only when an offset is beyond the final page.
- Load the representative run in the page statement instead of issuing a second run lookup.
- Load only the account and model option facets consumed by the service, using one portable query rather than four repeated aggregations.
- Add SQLite and PostgreSQL regression coverage for semantics and query counts, plus representative large-history benchmark and query-plan evidence.
- Add no schema, migration, setting, dependency, or API-shape change.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `automations`: require grouped run-history pages and filter options to preserve the existing dynamic cycle semantics while producing snapshot-consistent results with bounded database round trips.

## Impact

- Affected code: `app/modules/automations/repository.py` and the narrow service/options mapping if needed.
- Affected tests: automation API integration coverage on SQLite and PostgreSQL, plus query-count ratchets and benchmark/query-plan evidence.
- API request parameters and response schemas remain unchanged.
- Database models and the Alembic graph remain unchanged; no backfill or downgrade is required.
