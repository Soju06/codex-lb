## Why

The dashboard request logs table currently exposes error information in a way that is difficult to use during debugging. Long error messages are truncated into a narrow table cell, operators cannot reliably discover that more detail exists, and the UI does not provide a direct way to copy the error text or request identifier for follow-up investigation.

Request logs are an operator workflow, not decorative telemetry. When a request fails, the dashboard should make the failure easy to inspect, distinguish, and copy without forcing users to leave the page or guess at hidden affordances.

## What Changes

- Improve the dashboard request logs interaction model so error rows always expose a visible path to full request details.
- Add a request-detail surface for request-log rows that shows the full error code and error message alongside existing request metadata.
- Add copy actions for high-value debugging fields such as request id and full error text.
- Replace the current single-line error truncation with a richer preview that remains scannable in the table while preserving density.
- Preserve existing request-log filtering and pagination behavior while adding detail interactions.

## Impact

- Specs: `openspec/specs/frontend-architecture/spec.md`
- Frontend: dashboard recent-requests table, request-detail interaction state, copy affordances, accessibility and keyboard flow
- Backend: no API contract change required if the existing request-log payload remains sufficient
- Tests: frontend interaction coverage for request details, copy actions, and preview behavior
