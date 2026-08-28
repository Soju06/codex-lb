# Add Automations Scheduled Pings

## Why

Operators need a built-in way to schedule recurring ping traffic from selected accounts and models (for readiness checks and warm-up behavior) without manual intervention.

Today the dashboard has no automation surface, no schedule persistence, and no per-run visibility for these checks.

## What Changes

- add a new dashboard capability for automation jobs (`/api/automations`) with CRUD, enable/disable, and run history
- add a background scheduler that executes daily ping jobs at configured time and timezone
- add account failover within a job run so account-level rate limits do not terminate the run when other selected accounts are available
- add a new `Automations` tab in the SPA for managing jobs and viewing latest run outcomes/errors

### API scope (MVP)

- `GET /api/automations`
- `POST /api/automations`
- `PATCH /api/automations/{id}`
- `DELETE /api/automations/{id}`
- `POST /api/automations/{id}/run-now`
- `GET /api/automations/runs`
- `GET /api/automations/{id}/runs`
- `GET /api/automations/options`
- `GET /api/automations/runs/options`

Each automation job stores:

- `name`
- `enabled`
- `schedule.type` (`daily`)
- `schedule.time` (`HH:MM`, 24-hour)
- `schedule.timezone` (IANA timezone)
- `schedule.days` (`mon..sun`, at least one day; defaults to all weekdays)
- `schedule.thresholdMinutes` (`0..240`, dispatch spread window)
- `model`
- `reasoningEffort` (`minimal|low|medium|high|xhigh`, optional)
- `prompt`
- ordered `accountIds` (empty list means "all available accounts")

Run records expose:

- `scheduledFor`, `startedAt`, `finishedAt`
- `status` (`success`, `failed`, `partial`, `running`)
- `accountId` (account used for terminal attempt)
- `errorCode`, `errorMessage`

## Impact

- DB: new tables for automation jobs, account bindings, and run history
- Backend: new `app/modules/automations/*` module and startup lifecycle scheduler wiring
- Frontend: new route/page and header navigation entry (`/automations`)
- Testing: unit/integration coverage for scheduler timing, run claiming, failover, and UI flows

## Capabilities

### Added Capabilities

- `automations`

### Modified Capabilities

- `frontend-architecture`
