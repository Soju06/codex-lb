## Why

Operators can see `service_tier` in request logs, but they still cannot tell whether a request reached the proxy over HTTP or WebSocket. That makes it hard to verify that `Codex CLI` actually switched to websocket transport from the dashboard alone.

## What Changes

- Persist a `transport` field on `request_logs` for Responses proxy requests.
- Expose `transport` from `/api/request-logs`.
- Show transport in the dashboard recent requests table.
- Keep legacy rows without `transport` renderable.

## Impact

- Code: `app/db/models.py`, `app/modules/request_logs/*`, `app/modules/proxy/service.py`, `frontend/src/features/dashboard/*`
- Migrations: new Alembic revision for `request_logs.transport`
- Tests: request log API, Responses proxy integration, websocket proxy integration, dashboard component/schema tests
- Specs: `openspec/specs/responses-api-compat/spec.md`, `openspec/specs/frontend-architecture/spec.md`
