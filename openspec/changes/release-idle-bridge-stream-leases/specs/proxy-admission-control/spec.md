## ADDED Requirements

### Requirement: Stream leases reflect in-flight turns, not session lifetime

An HTTP bridge session's per-account stream lease MUST be held only while the session has in-flight work. When a session's last in-flight turn detaches — no queued requests, no admission waiters, and no pending requests — the session MUST release its account stream lease while remaining alive for reuse, so a warm idle upstream WebSocket does not occupy a per-account stream slot for its idle TTL. A turn admitted to a session holding no lease MUST reacquire one under normal cap admission before it is counted into the session queue, and a denied reacquisition MUST fail with the standard HTTP 429 `account_stream_cap` envelope so the recoverable capacity wait and client retry semantics apply unchanged. The stream recovery reserve MUST NOT be consulted at reacquisition, consistent with the reserve being a selection-time reserve. Session close MUST keep its existing lease settlement; a session that already released while idle has nothing further to settle.

#### Scenario: Finished turn returns the account's stream slot

- **GIVEN** a bridge session whose only in-flight turn completes
- **WHEN** the turn's stream finalizes and detaches
- **THEN** the session releases its account stream lease
- **AND** the session remains alive for reuse within its idle TTL

#### Scenario: Idle sessions do not starve new admissions

- **GIVEN** an account at its stream cap where some leases belong to idle sessions
- **WHEN** those sessions' turns complete
- **THEN** the freed slots admit new work immediately
- **AND** the freed slots are not held until the idle sessions' TTL expiry

#### Scenario: Next turn on an idle session passes cap admission

- **GIVEN** an idle bridge session that released its stream lease
- **WHEN** a new turn is admitted to that session
- **THEN** the session reacquires a stream lease before the turn is counted into the session queue

#### Scenario: Reacquisition denial uses the standard cap envelope

- **GIVEN** an idle bridge session whose account is at its stream cap
- **WHEN** a new turn's lease reacquisition is denied
- **THEN** the turn fails with HTTP 429 and `error.code = "account_stream_cap"`
- **AND** the recoverable account-capacity wait applies to the retry

#### Scenario: Busy sessions keep their lease

- **GIVEN** a bridge session with another turn still queued or pending
- **WHEN** one of its turns detaches
- **THEN** the session's stream lease is retained
