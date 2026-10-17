## Why

Direct Responses WebSocket scope teardown currently reuses the generic
`_TASK_CANCEL_TIMEOUT_SECONDS` value as its entire normal-operation cleanup
budget. That value is intentionally one second for individual task
cancellation, but scope teardown can also have to finalize request logs,
release response-create ownership, and release the account connection lease.
Under ordinary load those operations can exceed one second, producing
`Websocket scope cleanup exceeded its remaining drain budget` even when the
server is not draining. The cleanup task remains tracked, but the warning and
unfinished teardown increase the chance of follow-up reconnect churn.

## What Changes

- Give normal-operation WebSocket scope teardown its own fixed five-second
  bounded budget.
- Keep the existing one-second generic task-cancellation timeout for ordinary
  child-task waits.
- Continue using the remaining shared shutdown deadline whenever process drain
  is active; the new budget must not extend shutdown.
- Add a route-level cancellation regression proving that cleanup which takes
  longer than the generic task timeout can still finish within the scope budget
  and does not leave an orphaned cleanup task.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Direct Responses WebSocket scope cleanup has a
  separate bounded normal-operation budget while preserving the shared
  shutdown deadline and task ownership guarantees.

## Impact

The change is limited to the direct Responses WebSocket finalizer, its focused
unit coverage, and the OpenSpec contract. It adds no setting, dependency,
database migration, API shape, upstream watchdog change, or retry policy.
