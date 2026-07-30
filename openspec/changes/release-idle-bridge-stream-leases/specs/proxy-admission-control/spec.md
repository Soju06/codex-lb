## ADDED Requirements

### Requirement: Stream leases reflect in-flight turns, not session lifetime

An HTTP bridge session's per-account stream lease MUST be held only while the session has in-flight work. When a session's last in-flight turn detaches — no queued requests, no admission waiters, and no pending requests — the session MUST release its account stream lease while remaining alive for reuse, so a warm idle upstream WebSocket does not occupy a per-account stream slot for its idle TTL. Cancellation MUST NOT interrupt that idle lease settlement after the lease is detached from the session. A turn admitted to a session holding no lease MUST reacquire one under normal cap admission before it is counted into the session queue, and a denied reacquisition MUST fail with the standard HTTP 429 `account_stream_cap` envelope so the recoverable capacity wait and client retry semantics apply unchanged. Reacquisition MUST carry the turn's usage-budget token estimate into the lease, matching initial bridge selection and reconnect, so capacity-weighted routing pressure continues to see turns running on reused warm sessions. The stream recovery reserve MUST NOT be consulted at reacquisition, consistent with the reserve being a selection-time reserve. Session close MUST keep its existing lease settlement; a session that already released while idle has nothing further to settle.

The lease remains per-session, matching the pre-existing lease lifecycle: a session MUST hold at most one stream lease at a time, and turns queued on a session that already holds a lease MUST NOT acquire additional leases — queued turns multiplex over the session's single upstream stream, which is what the per-account stream cap bounds. If the session closes while a reacquisition is in flight, the freshly acquired lease MUST be released back rather than installed on the closed session, and the turn MUST fail with the standard closed-bridge error envelope. Cancellation MUST NOT interrupt release of that detached lease. A submit MUST be registered as in-flight work (admission waiter) atomically with its lease reacquisition, so a completed turn's finalizer running concurrently cannot observe the session as idle and release the reacquired lease before the new turn is counted into the session queue. Any failure after waiter registration and before queue admission MUST remove that waiter and settle an otherwise-idle lease. Reconnect and reacquisition MUST serialize changes to the session lease so a reconnect lease cannot be overwritten and leaked by a concurrent reacquisition. Cancellation MUST NOT interrupt settlement of a lease detached during reconnect replacement. If prewarm fails after the upstream reader closes the session and defers retirement for that admission waiter, removing the final waiter MUST retire the closed session and release its stream lease. Prewarm cancellation MUST NOT interrupt removal of the admission waiter or settlement of an otherwise-idle stream lease.

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

#### Scenario: Close racing reacquisition does not leak the slot

- **GIVEN** an idle bridge session whose stream lease reacquisition is awaiting cap admission
- **WHEN** the session is closed or evicted before the acquisition completes
- **THEN** the freshly acquired lease is released back to the account
- **AND** the turn fails with the standard closed-bridge error envelope

#### Scenario: Cancellation during close-race settlement does not leak the slot

- **GIVEN** a session closes while reacquisition is awaiting cap admission
- **AND** the submit is cancelled while the freshly acquired lease is being returned
- **WHEN** lease settlement completes
- **THEN** cancellation propagates only after the lease is released

#### Scenario: Stale finalizer cannot release a lease reacquired for a new turn

- **GIVEN** a warm session whose new turn has reacquired a stream lease but is not yet counted into the session queue
- **WHEN** a previous turn's finalizer runs its idle-release check concurrently
- **THEN** the session is not considered idle
- **AND** the reacquired lease is retained for the new turn

#### Scenario: Failed queue admission removes its waiter

- **GIVEN** a submit has registered an admission waiter before queue admission
- **WHEN** its final lease check fails
- **THEN** the admission waiter is removed
- **AND** an otherwise-idle session releases its stream lease

#### Scenario: Reconnect racing reacquisition retains one lease

- **GIVEN** a reconnect and idle-session lease reacquisition overlap
- **WHEN** both acquire a stream lease before either operation completes
- **THEN** the session retains exactly one of those leases
- **AND** the losing lease is released immediately

#### Scenario: Queued turns share the session's single stream slot

- **GIVEN** a bridge session that holds a stream lease for an active turn
- **WHEN** additional turns are admitted to the session queue
- **THEN** no additional stream leases are acquired
- **AND** the session continues to hold exactly one stream lease

#### Scenario: Prewarm failure retires a closed session after its waiter leaves

- **GIVEN** a new turn has reacquired a stream lease and registered an admission waiter
- **AND** the upstream reader closes the session during prewarm and defers retirement for that waiter
- **WHEN** prewarm fails and the final admission waiter is removed
- **THEN** the closed session is retired
- **AND** its stream lease is released

#### Scenario: Prewarm cancellation completes lease cleanup

- **GIVEN** a new turn has reacquired a stream lease and registered an admission waiter
- **WHEN** the downstream task is cancelled during prewarm
- **THEN** cleanup removes the admission waiter before propagating cancellation
- **AND** an otherwise-idle session releases its stream lease

#### Scenario: Grouped terminal errors release an abandoned session's lease

- **GIVEN** a bridge session whose only pending turns are detached follow-ups (no downstream consumers remain)
- **WHEN** a grouped terminal error (for example `previous_response_not_found`) settles all of them together
- **THEN** the session releases its account stream lease
- **AND** the freed slot admits new work without waiting for session close or idle TTL expiry

#### Scenario: Busy sessions keep their lease

- **GIVEN** a bridge session with another turn still queued or pending
- **WHEN** one of its turns detaches
- **THEN** the session's stream lease is retained
