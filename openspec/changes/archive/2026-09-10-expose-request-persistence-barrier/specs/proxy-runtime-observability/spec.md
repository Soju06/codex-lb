## ADDED Requirements

### Requirement: Drain status distinguishes request-persistence ownership

The loopback drain-status response MUST expose the state of tracked proxy request-persistence ownership separately from HTTP bridge activity. It MUST expose `checks.request_persistence_state` as `pending`, `drained` or `unknown`. When the pending count is known, the response MUST expose `checks.request_persistence_pending` as a nonnegative decimal string without task names, request IDs, API-key IDs or content. Missing, invalid or unsupported observation MUST report `unknown` and omit the pending count. Zero registered owners MUST report `drained` and count `0`; a positive count MUST report `pending`.

#### Scenario: HTTP response ends before reservation settlement

- **GIVEN** an admitted native HTTP Responses stream has transferred a real reservation to tracked settlement
- **AND** its response scope and bridge pending queue are empty
- **WHEN** the finalizer remains unfinished
- **THEN** drain status reports pending request persistence
- **AND** existing global and bridge counters retain their established meanings

#### Scenario: Registered follow-up work retains ownership

- **GIVEN** tracked settlement completion schedules an owned fallback or retry
- **AND** completion callbacks have not yet finished transferring ownership
- **WHEN** drain status observes that handoff
- **THEN** it does not report drained between the original task and its follow-up owner
- **AND** it reports drained only after no classified request-persistence ownership remains

#### Scenario: Observation is unavailable

- **WHEN** request-persistence ownership cannot be observed reliably
- **THEN** drain status reports unknown rather than a zero pending count or drained state

### Requirement: Persistence observation preserves execution semantics

Drain-status observation MUST NOT await persistence completion, cancel work, change admission state, start database operations, or emit a shutdown-timeout warning for ordinary pending work. It MUST preserve the existing detached-settlement and HTTP bridge cleanup-count contracts.

#### Scenario: Repeated observation during slow settlement

- **GIVEN** a reservation finalizer remains held beyond the operator's observation interval
- **WHEN** drain status is polled repeatedly
- **THEN** the finalizer remains owned and uncancelled
- **AND** observation remains bounded
- **AND** clearing reversible drain permits the admitted request and settlement to complete exactly once
