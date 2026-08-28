## Why

codex-lb already persists the upstream reasoning-token count reported for completed direct Codex subscription responses, but the dashboard only renders total, cached-input, and output token totals. Operators cannot see the reasoning subset in request history or aggregate it over a report window without querying the database directly.

## What Changes

- Show the persisted reasoning-token count in request-log rows and request details.
- Add reported reasoning-token totals and summary coverage to the reports response.
- Render reported reasoning totals in the reports summary, daily breakdown, and CSV export.
- Keep reasoning tokens as a subset of output tokens so existing total-token and cost calculations do not double-count them.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: Request history and reports expose the reasoning-token subset already recorded by the proxy.

## Impact

Reports API aggregation, dashboard schemas and components, localized labels, user-facing usage-reporting documentation, and focused backend/frontend tests. No database migration, configuration, routing, pricing, or proxy-protocol change.
