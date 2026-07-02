# Context

`request_logs` serves multiple jobs:

- Recent operational debugging through `/api/request-logs`.
- Dashboard and API-key usage aggregations.
- Continuity owner lookups for `previous_response_id`.

That means raw rows cannot be deleted casually. The safe retention model is to
keep raw rows for recent windows and preserve old totals in coarse aggregates.
The first implementation intentionally does not switch dashboards to aggregate
reads; it creates the data-retention primitive and keeps deletion opt-in.

Example:

An operator enables a 30-day raw retention window. On July 31, rows before
July 1 are eligible. The retention job aggregates each eligible UTC day by API
key, account, model, status, service tier, transport, request kind, and
soft-deleted state. Only after the aggregate upsert succeeds does it delete
those raw rows. Rows from July 1 onward remain untouched for request detail,
7-day API-key charts, and continuity lookups.
