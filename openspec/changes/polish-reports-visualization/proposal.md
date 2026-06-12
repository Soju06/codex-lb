## Why

The reports surface had drifted across several small follow-up changes and no longer had one coherent OpenSpec source of truth. Operators need `/reports` to use the browser's local calendar consistently, keep the active date range visually honest, prevent invalid future-date inputs, show continuous day-by-day reporting even when some days have no data, and expose previous-window comparison only when the backend can prove the baseline window is fully covered.

## What Changes

- Consolidate the reports visualization work into one surviving OpenSpec change.
- Keep the visible `/reports` controls for presets, dates, account, and model, but clear the active preset highlight after manual date edits.
- Disallow future dates in the report start and end inputs.
- Send the browser's current IANA timezone on reports requests when available, and define the reports endpoint's timezone-aware date-range and daily-bucketing behavior.
- Fill missing calendar days in the daily breakdown with zero-valued rows for the selected date window, keep ISO calendar dates, and keep only the body scrollable with a seven-row viewport.
- Define the previous-window comparison contract for report summary cards and require comparison to stay hidden unless the immediately preceding window is fully covered.
- Keep the model distribution donut cost-based while omitting any center value.
- Use equal left and right horizontal padding in the `Cost by Day` and `Tokens by Day` chart plots.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: refine `/reports` filter, request, summary-card, table, donut, chart, and endpoint requirements for the consolidated behavior.

## Impact

- Frontend: `frontend/src/features/reports/components/{reports-page,reports-filters,reports-summary-cards,daily-detail-table,model-distribution-donut,cost-per-day-chart,tokens-per-day-chart}.tsx`
- Frontend utilities/hooks: `frontend/src/features/reports/{date,hooks,use-reports.ts,api.ts}`
- Backend: `app/modules/reports/{api,service,repository,schemas}.py`
- Frontend and backend tests covering reports filters, summary comparison, timezone requests, and timezone-aware daily bucketing
- OpenSpec: `openspec/specs/frontend-architecture/spec.md`
