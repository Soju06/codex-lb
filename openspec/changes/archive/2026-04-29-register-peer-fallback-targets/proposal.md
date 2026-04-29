## Why

Peer `codex-lb` fallback is currently configured only through environment variables, which makes it awkward to add, remove, or disable peers after deployment. Operators need a dashboard-managed registration flow similar to accounts so fallback peers can be managed without editing env files or restarting the service.

## What Changes

- Add dashboard-managed peer fallback targets with create, list, update, delete, and enable/disable operations.
- Store registered peer fallback targets in the application database.
- Use enabled database targets for peer fallback when any are registered; preserve the existing environment-variable peer list as the bootstrap/default source when no database targets exist.
- Add a Settings page management section for registering peer `codex-lb` base URLs.
- Keep existing peer fallback eligibility, loop-prevention, health-check, and HTTP/SSE-only behavior.

## Capabilities

### New Capabilities

- `peer-fallback-targets`: Dashboard and runtime management of peer `codex-lb` fallback base URLs.

### Modified Capabilities

- `frontend-architecture`: The Settings page gains peer fallback target management.
- `database-migrations`: The database schema gains persistent peer fallback target storage.

## Impact

- Backend: new database model, repository/service/API, and peer fallback runtime target resolution.
- Frontend: new Settings section, API client, schemas, and React Query hook for peer fallback targets.
- Database: new Alembic migration for peer fallback target rows.
- Tests: unit and integration coverage for persistence, API behavior, runtime fallback target selection, and UI contract parsing.
