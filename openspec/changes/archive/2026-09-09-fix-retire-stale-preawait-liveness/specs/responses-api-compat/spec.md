## ADDED Requirements

### Requirement: Stale bridge retirement rechecks liveness after suspension

Before closing and unregistering a stale HTTP bridge session, the service MUST re-sample pending request liveness after retry-circuit bookkeeping awaits. A response event, response id, or equivalent response-created signal newly observed after the caller's pre-suspension snapshot MUST prevent stale retirement. A session that remains eventless MUST still be retired. Retirement entered from the reader-failure funnel, or for a session that was already closed when its last admission waiter cancelled, MUST NOT be revived by post-suspension signals: its pending turns were already terminally failed and its reader is condemned, and the completed-response anchor can be moved by durable-anchor rehydration without any upstream evidence.

#### Scenario: First response event arrives during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and then suspends for retry-circuit bookkeeping
- **AND** a pending turn receives its first response event before the close decision
- **THEN** the final decision samples pending state under `session.pending_lock`
- **AND** makes the registry decision under `_http_bridge_lock`
- **AND** the session remains registered, open, and reusable

#### Scenario: Detached generation is not revived by post-suspension liveness

- **WHEN** stale retirement observes post-suspension liveness for a session
- **AND** the acquisition loop has already detached that session from the registry during the suspension
- **THEN** the final decision does not clear the detached generation's retirement flags
- **AND** the detached generation still receives its bounded close so its socket, leases, and capacity slot are released

#### Scenario: Fence raised during suspension survives post-suspension liveness

- **WHEN** stale retirement suspends for retry-circuit bookkeeping
- **AND** a fence owner sets reconnect-requested or retire-after-drain on the still-registered session during the suspension while also advancing the event generation
- **THEN** the final decision does not clear the fence
- **AND** the session is unregistered and receives its bounded close instead of being revived

#### Scenario: Prelude-only upstream event during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and then suspends for retry-circuit bookkeeping
- **AND** an upstream event that advances only the session event generation arrives during the suspension
- **THEN** the final decision observes the generation change against the entry-time baseline
- **AND** the session remains registered, open, and reusable

#### Scenario: Reader-failure retirement is not revived by durable-anchor rehydration

- **WHEN** the reader-failure funnel retires a session whose pending turns were already terminally failed
- **AND** a concurrent durable-anchor rehydration moves the session's completed-response anchor during the retirement suspension
- **THEN** the final decision does not treat the anchor movement as liveness
- **AND** the session is unregistered and receives its bounded close

#### Scenario: Session remains eventless during retry-circuit suspension

- **WHEN** stale retirement samples zero response events and suspends for retry-circuit bookkeeping
- **AND** no pending turn receives a response or response-created signal
- **THEN** the final decision retires and unregisters the session
