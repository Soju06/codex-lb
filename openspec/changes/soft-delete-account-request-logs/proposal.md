## Why

Deleting an account currently removes its historical request logs, which erases audit and metrics history. Operators need account deletion to hide stale account-linked rows from the dashboard request-log table while preserving aggregate usage and cost metrics.

## What Changes

- Mark request-log rows associated with a deleted account instead of physically deleting them.
- Hide soft-deleted request-log rows from the dashboard request-log list and its filter facets.
- Keep metrics and aggregate usage calculations unchanged so deleted-account history still contributes.
- Group API-key account usage donut rows from deleted accounts under `Deleted Accounts`.
- Render the `Deleted Accounts` donut slice and legend dot with the same color used for `Used` in the dashboard donut.

## Impact

- Code: `app/db/models.py`, `app/db/alembic/versions/*`, `app/modules/accounts/repository.py`, `app/modules/request_logs/*`, `app/modules/api_keys/*`, `frontend/src/features/apis/components/api-account-cost-donut.tsx`
- Tests: repository/account deletion, request-log listing/facets, API-key account usage grouping/color, migration drift
- Specs: `frontend-architecture`, `api-keys`
