# Proposal: add-reports-page

## Why
Operators need a dashboard view of cost and request trends over time, with filtering by date range, model, account, and user-agent context.

## What Changes
- Add `GET /api/reports` with aggregated cost, request, model, user-agent, and account reporting.
- Add a new dashboard `/reports` page with summary cards, daily charts, model and user-agent distribution donuts, center totals that follow the active metric toggle, and a CSV export path.
- Render unknown/deleted-account request history safely by allowing report account identifiers to be nullable.

## Capabilities

### Modified Capabilities

- `frontend-architecture`: dashboard API and view now include the reports page and report payload shape.

## Impact

- Backend: `app/modules/reports/{api,repository,schemas,service}.py`
- Frontend: `frontend/src/features/reports/{api,schemas,components,*}`
- Tests: reports integration tests around null `accountId` and SQLite date aggregation behavior.
