## Why

Reports currently derive the per-day average divisor from UTC-converted filter
boundaries. For inclusive local calendar ranges that cross some IANA offset
changes, this makes `avgCostPerDay` and `avgRequestsPerDay` use one or three
days even though the operator selected two.

## What Changes

- Calculate both per-day report averages from the exact inclusive local
  calendar range selected by `start_date` and `end_date`.
- Add focused service coverage for both affected Casablanca transitions and
  unchanged UTC, stable-offset, and invalid-timezone behavior.
- Preserve report filtering, daily bucketing, totals, comparisons, range
  validation, and all other report metrics.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Define the local-calendar divisor used by the
  Reports API per-day summary averages.

## Impact

- Backend: `app/modules/reports/service.py`
- Tests: `tests/unit/test_reports_service.py`
- API contract: `GET /api/reports` summary averages only
- No schema, migration, repository, dependency, setting, or frontend code
  changes
