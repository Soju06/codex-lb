## Why

The dashboard request logs table currently shows the account label but not the account plan tier. Operators debugging mixed free/plus/team traffic have to cross-reference the accounts page to understand which plan generated a request or error.

## What Changes

- Expose `planType` on `GET /api/request-logs` by deriving it from the related account when available.
- Show the account plan tier in the dashboard recent requests table as a visible badge or column.
- Keep legacy request-log rows without an associated account renderable.

## Impact

- Code: `app/db/models.py`, `app/modules/request_logs/*`, `frontend/src/features/dashboard/*`
- Migrations: none
- Tests: request-log API integration, repository loading, dashboard schema/component coverage
- Specs: `openspec/specs/frontend-architecture/spec.md`
