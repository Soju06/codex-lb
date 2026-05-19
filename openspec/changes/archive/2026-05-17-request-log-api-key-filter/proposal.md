## Why

Operators can already see the API key name in the dashboard request logs table, but they cannot filter the table to isolate traffic from one or more API keys. Search can match API key names, but it is not a reliable replacement for a dedicated facet because API key names are not stable identifiers and search does not expose the active filter state clearly.

## What Changes

- Add dashboard request-log filtering by stable API key identifier.
- Expose API key filter options on `GET /api/request-logs/options` with operator-friendly labels.
- Keep request-log list filtering on the `request_logs.api_key_id` column so the hot list query does not require related-table joins.
- Keep the API key facet expandable by not self-filtering the API key options list.

## Impact

- Affects the dashboard request-log backend API, repository filters, and frontend filter state/UI.
- Adds backward-compatible request-log filter parameters and filter-option payload fields.
- Improves operator debugging workflows for shared deployments that serve multiple API keys.
