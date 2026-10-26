## Why

The Dashboard page carries everything at once: summary cards, credit donuts, weekly pace, one card per account, and a filterable request-log table with facet options and projections. Operators who only want "is the pool healthy and is traffic flowing" pay for all of it on every poll. They need a compact, visual overview that loads two endpoints, plus a live request feed.

The earlier Accounts plan overview (`/accounts/overview`, never released) answered part of this; this change replaces it with one overview tab on the Dashboard that also shows request activity and recent requests.

## What Changes

- A read-only Overview tab at `/dashboard/overview`, reached through "Dashboard | Overview" tabs on both dashboard routes. No core navigation item.
- Activity: 24h / 7d / 30d timeframe, summary cards with previous-window deltas and sparklines, and an activity chart switchable between requests (with errors), tokens, and error rate.
- Accounts: a plan selector; a status tile and one quota tile per window with the average used percent, drawn per account for up to 100 accounts and as distributions (status-share bar, ten-bucket histogram) for larger plans such as 3,000+ free accounts.
- Requests: the Dashboard's full request-log table (all columns, details, pages) with an auto-refresh switch, an All / Success / Errors filter, and page statistics (errors, average and p95 latency, tokens, cache share).
- `/accounts/overview` (an unreleased plan-grouped accounts overview from a fork build) redirects to `/dashboard/overview`; the Accounts page keeps no view tabs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Dashboard overview tab (replaces the unreleased Accounts plan overview).

## Impact

Frontend only. Reuses `GET /api/dashboard/overview` and `GET /api/request-logs`. No backend, API, schema, migration, setting, dependency, or navigation item changes.
