## Why

The dashboard request logs table currently shows the account label but not the account plan tier. Operators debugging mixed free/plus/team traffic have to cross-reference the accounts page to understand which plan generated a request or error.

## What Changes

- Persist a nullable `plan_type` snapshot on `request_logs` when the log row is written.
- Expose `planType` on `GET /api/request-logs` from the persisted request-log snapshot rather than the current account row.
- Show the account plan tier in the dashboard recent requests table as a visible badge or column.
- Keep legacy request-log rows without an associated account renderable.

## Impact

- Code: `app/db/models.py`, `app/modules/request_logs/*`, `frontend/src/features/dashboard/*`
- Migrations: add nullable `request_logs.plan_type`
- Tests: request-log API integration, repository loading, dashboard schema/component coverage
- Specs: `openspec/specs/frontend-architecture/spec.md`
