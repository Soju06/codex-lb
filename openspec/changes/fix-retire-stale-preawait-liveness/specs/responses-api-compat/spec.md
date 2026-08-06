## ADDED Requirements

### Requirement: Stale bridge retirement rechecks liveness after suspension

Before closing and unregistering a stale HTTP bridge session, the service MUST re-sample pending request liveness after retry-circuit bookkeeping awaits. A response event, response id, or equivalent response-created signal newly observed after the caller's pre-suspension snapshot MUST prevent stale retirement. A session that remains eventless MUST still be retired.

#### Scenario: First response event arrives during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and then suspends for retry-circuit bookkeeping
- **AND** a pending turn receives its first response event before the close decision
- **THEN** the final decision observes the event under the bridge and pending-state locks
- **AND** the session remains registered, open, and reusable

#### Scenario: Session remains eventless during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and suspends for retry-circuit bookkeeping
- **AND** no pending turn receives a response or response-created signal
- **THEN** the final decision retires and unregisters the session
