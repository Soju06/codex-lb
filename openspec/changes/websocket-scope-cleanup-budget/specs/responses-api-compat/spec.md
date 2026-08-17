# responses-api-compat Delta

## ADDED Requirements

### Requirement: Direct WebSocket scope cleanup has a bounded normal-operation budget

When a direct Responses WebSocket scope exits while the process is not using an
active shutdown drain deadline, the proxy MUST allow its existing scope
finalization task a fixed five-second bounded observation budget, separate from
the one-second generic child-task cancellation timeout. The finalizer MUST
continue to own request finalization and lease cleanup through that budget. If
the budget expires, the proxy MUST preserve the existing cancellation result,
leave unfinished cleanup tracked by the existing cleanup-task registry, and
MUST NOT cancel or silently abandon that cleanup solely because the observation
budget expired.

When an active shutdown drain deadline exists, the proxy MUST use the remaining
shared drain deadline instead of the normal-operation budget, so normal cleanup
allowance MUST NOT extend process shutdown.

#### Scenario: normal scope cleanup outlives generic child cancellation

- **GIVEN** a direct Responses WebSocket scope is cancelled while its existing
  request finalization takes longer than the generic one-second child-task
  cancellation timeout
- **AND** the finalization completes within the five-second normal-operation
  scope budget
- **WHEN** scope cleanup runs
- **THEN** the finalizer completes and request/lease ownership is released
- **AND** the scope preserves its cancellation result
- **AND** no cleanup task remains orphaned after the finalizer completes

#### Scenario: shutdown drain remains the upper bound

- **GIVEN** a direct Responses WebSocket scope is cancelled while an active
  shutdown drain deadline has less than five seconds remaining
- **WHEN** scope cleanup runs
- **THEN** the remaining shared drain deadline remains the upper bound
- **AND** the normal-operation five-second budget does not extend shutdown
