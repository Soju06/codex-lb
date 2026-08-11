## Why

Warm-now compares quota reset epochs from usage history against the current
server time before deciding whether a short-window account is already active.
On non-UTC hosts, converting the planner's naive UTC `utcnow()` value with
`datetime.timestamp()` interprets that naive value as local time and can make a
past reset look future-dated. Operators then see a due manual warm-now request
skip as `account_window_already_active` instead of executing.

## What Changes

- Compare warmup reset epochs against a current epoch derived from naive UTC,
  independent of the process local timezone.
- Strengthen the route-level warm-now regression by running the due-reset path
  under a simulated UTC+ process timezone with cleanup.
- Document the observable behavior under the quota phase planner contract.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `quota-phase-planner`: Warm-now reset gates must treat persisted reset epochs
  as UTC instants regardless of the process timezone.

## Impact

- Backend: `app/modules/quota_planner/warmup.py`
- Tests: `tests/integration/test_quota_planner_api.py`
- Contract: Manual warm-now execution eligibility on non-UTC hosts
- No API schema, database migration, dependency, setting, dashboard, or
  successful-response shape change
