## Why

First-run dashboard surfaces tell operators to "Adjust filters" or show a
zero-line Reports chart when the fleet is empty. The legacy `/firewall`
redirect lands on Settings with Advanced collapsed, so the firewall section
stays hidden.

## What Changes

- Accounts, APIs, and request-log empty copy distinguish first-run (no rows)
  from filtered-empty (rows exist but none match).
- Dashboard empty-account cards/list include a CTA to `/accounts`.
- Reports line charts show a no-data empty state when the report payload has
  no daily rows, instead of a continuous zero-filled chart.
- `/firewall` redirects to `/settings?advanced=1#firewall`, which expands
  Advanced and scrolls to the firewall section. Plain `/settings` stays
  collapsed by default.

## Capabilities

### New Capabilities

- None

### Modified Capabilities

- `frontend-architecture`: first-run vs filtered empty states, dashboard
  empty-account CTA, reports no-data charts, and `/firewall` Advanced deeplink.

## Impact

Dashboard SPA only: empty-state copy, optional EmptyState action, Reports
chart empty rendering, Settings Advanced open-from-query/hash, `/firewall`
redirect target, and i18n (`en`/`ko`/`zh-CN`). No API, database, proxy, or
nav-budget changes.
