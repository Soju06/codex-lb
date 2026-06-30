## Context

The reports page is intended as a read-only dashboard surface for historical billing analysis.
It uses `/api/reports` to return:

- Daily totals (`date`, request count, token counts, costs)
- Model totals (cost, request count, and percent share)
- User-agent totals (cost, request count, and percent share)
- Account totals (`accountId`, alias, cost, request count)

`accountId` can be `null` when the request log row has no surviving account (deleted account rows or legacy data). The UI should treat it as a nullable, user-visible bucket that keeps history intact.

## Decisions

### 1) Add explicit nullable type for `accountId`
The backend report row type now uses a nullable account identifier so historical rows from deleted/unknown accounts do not crash response validation.

### 2) Keep SQLite date extraction backend-side
For SQLite, `/api/reports` uses `strftime("%Y-%m-%d", requested_at)` for date grouping to avoid SQLAlchemy comparator limitations on `Cast(...).substring(...)`.

### 3) Reuse existing dashboard styling patterns
The reports page reuses existing dashboard chart/table/surface patterns and exposes filtering by date, model, and account through existing query param conventions.

### 4) Keep donut totals tied to the active metric
The model and user-agent distribution donuts show a center label/value stack with `Total` above the active metric value.
When the toggle is set to `cost`, the center and legend values show compact USD totals such as `$1.43K`.
When the toggle is set to `req`, the center and legend values show compact request totals such as `1.5B`.
