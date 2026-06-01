# lazy-dashboard-projections

## Why

Dashboard startup was blocked by quota projection work in `GET /api/dashboard/overview`.
On live data, the heavy depletion and weekly-credit projection pass can take around 12
seconds, even though the dashboard's primary cards, donuts, accounts, and request logs
can render from cheaper queries much earlier.

## What Changes

- Keep `GET /api/dashboard/overview` focused on fast dashboard data.
- Add `GET /api/dashboard/projections` for depletion safe-lines and weekly-credit pace.
- Fetch projections in the SPA after overview data is available, so the web interface
  can render first and fill projection-only elements in the background.
- Keep frontend compatibility with older overview payloads that still include projection
  fields.

## Impact

The dashboard can show its main interface before long-running projection calculations
finish. Projection data still refreshes, but it no longer blocks initial page load.
