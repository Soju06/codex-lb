## Why

Deleting an account currently removes its historical request logs, which erases audit and metrics history. Operators need account deletion to hide stale account-linked rows from the dashboard request-log table while preserving aggregate usage and cost metrics.

## What Changes

- Mark request-log rows associated with a deleted account instead of physically deleting them.
- Hide soft-deleted request-log rows from the dashboard request-log list and its filter facets.
- Keep metrics and aggregate usage calculations unchanged so deleted-account history still contributes.
- Group API-key account usage donut rows from deleted accounts under `Deleted Accounts`.

## Impact

- Code: `app/db/models.py`, `app/db/alembic/versions/*`, `app/modules/accounts/repository.py`, `app/modules/request_logs/*`, `app/modules/api_keys/repository.py`
- Tests: repository/account deletion, request-log listing/facets, API-key account usage grouping, migration drift
- Specs: `frontend-architecture`, `api-keys`
