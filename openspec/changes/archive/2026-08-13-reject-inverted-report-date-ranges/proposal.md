## Why

`GET /api/reports` currently accepts a `start_date` after `end_date`, performs report repository work, and returns a plausible empty report. The Reports UI can also submit that invalid state, which can mislead operators into treating an input error as a real zero-usage period.

## What Changes

- Reject inverted report date bounds in the Reports service before any repository operation and map the domain failure to a stable dashboard HTTP 400 error.
- Constrain the Reports date inputs reciprocally for routine picker use.
- Expose a linked accessible invalid state when typed or bypassed input still produces an inverted range.
- Suppress report data and report-filter-option queries while the range is invalid, then refresh cleanly after either bound is corrected.
- Add backend and frontend regression coverage while preserving valid one-day and 730-day ranges.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `frontend-architecture`: Define authoritative Reports date-order validation and accessible Reports UI prevention, query suppression, and recovery behavior.

## Impact

- Affected backend: `app/modules/reports/service.py`, `app/modules/reports/api.py`, and Reports API/service tests.
- Affected frontend: Reports filters, page query gating, localized validation copy, and Reports component/MSW tests.
- No changes to the 730-day limit, presets, timezone semantics, report aggregation or repository queries, charts, schema, migrations, settings, or navigation.
