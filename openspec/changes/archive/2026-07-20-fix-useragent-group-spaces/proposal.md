## Why

`request_logs.useragent_group` currently truncates at the first whitespace-delimited token, so `Codex Desktop/...` is grouped as `Codex` instead of `Codex Desktop`. Existing rows also need the same normalization so historical reports and new request logs agree.

## What Changes

- Normalize the derived user-agent group by trimming the user-agent before taking the content before the first `/`, preserving spaces within that content.
- Treat a missing or blank trimmed user-agent as `NULL`.
- Add a shared parser regression test for the supplied Codex Desktop user-agent.
- Add an Alembic data migration that recomputes every existing row from `useragent` for SQLite and PostgreSQL.
- Keep the data migration idempotent and make its downgrade a no-op because the previous derived values cannot be recovered.
- Add migration regression coverage and focused verification tasks.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: Define the trimmed, slash-delimited normalization contract for persisted request-log user-agent groups.
- `database-migrations`: Require the historical request-log user-agent-group data migration and its SQLite/PostgreSQL/idempotence behavior.

## Impact

The shared request-log user-agent parser, request-log parser tests, Alembic revision chain, and migration regression tests are affected. No API shape or configuration changes are introduced.
