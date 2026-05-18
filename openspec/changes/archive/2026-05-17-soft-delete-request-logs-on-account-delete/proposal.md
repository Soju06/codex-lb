## Why

Deleting an account currently hard-deletes its `request_logs` twice: the application explicitly deletes matching rows and the `request_logs.account_id` foreign key still cascades on account removal. That breaks historical request metrics and removes request-log evidence operators may still need for aggregate dashboards.

## What Changes

- Soft-delete `request_logs` when an account is deleted by marking a nullable `deleted_at` timestamp and detaching the row from the deleted account.
- Hide soft-deleted request-log rows from the dashboard request-log list and filter options.
- Preserve existing dashboard overview metrics and request-log aggregates so soft-deleted rows still contribute to historical counts, token totals, costs, and error summaries.
- Replace the `request_logs.account_id -> accounts.id` cascade path with `ON DELETE SET NULL` so database-level account deletion does not erase historical request logs.
- Add an index for the dashboard request-log list path that now filters on `deleted_at IS NULL`.

## Impact

- Code: `app/db/models.py`, `app/modules/accounts/repository.py`, `app/modules/request_logs/repository.py`
- Migrations: add `request_logs.deleted_at`, replace the request-log account FK delete action, add a dashboard list index
- Tests: account deletion, request-log API visibility, dashboard overview metrics, migration schema/index/FK coverage
- Specs: `openspec/specs/frontend-architecture/spec.md`, `openspec/specs/database-migrations/spec.md`
