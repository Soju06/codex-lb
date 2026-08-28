## MODIFIED Requirements

### Requirement: Stream reservation settlement is detached from the response path

Settling a stream API-key reservation MUST NOT block the response/stream close,
with one deliberate exception: when a keyed websocket stream terminates with an
account-health error, the finalizer MUST wait for the settlement to commit
before the load-balancer health write (the settlement-ordering invariant), so
that error path intentionally blocks on settlement. If the primary settlement
fails, the finalizer MUST wait for fallback release to commit before recording
account health. If neither operation confirms settlement, the account-health
write MUST remain unapplied. Tracked persistence ownership MUST remain
registered through an ordering-sensitive fallback release, including
cancellation before the primary coroutine starts or during that release, so
graceful shutdown drains both phases. When the existing stream-retry path
deliberately defers an
account-health penalty until the same ordering-sensitive settlement, it MUST
likewise apply neither that penalty nor an immediately following terminal health
write unless settlement is confirmed, and it MUST NOT start a second settlement
for the transferred reservation. The HTTP bridge's pre-created retry handling
(model-capacity wait, owner-pinned quota, and generic retryable pre-created
failures) MUST likewise defer a keyed request's classified account-health
write until that request's reservation settles or its fallback release
commits, MUST leave the deferred write unapplied when neither confirms, and
MUST keep the immediate write for unkeyed requests. After a committed
settlement or fallback release, deferred account-backoff writes and deferred
stream-health writes MUST drain on independent lanes so a failure in one
cannot orphan the other, and a deferred health write that itself fails MUST be
logged and dropped without aborting the remaining terminal finalization. In
all other cases the settlement MUST run as
a tracked background task; when it fails or is cancelled, the reservation MUST
still be released by the tracking fallback, and the request's finalization path
MUST NOT double-release a transferred settlement. Reservations MUST continue to
count toward key limits until finalized or released, so deferred settlement can
never admit usage a synchronous settlement would have rejected.

#### Scenario: Response close precedes settlement completion

- **GIVEN** a keyed stream whose settlement transaction is still running
- **WHEN** the stream closes
- **THEN** the close does not wait for the settlement
- **AND** the settlement finalizes the reservation exactly once in the background

#### Scenario: Failed detached settlement still releases the reservation

- **GIVEN** a detached settlement whose finalize raises
- **WHEN** the settlement task completes
- **THEN** the tracking fallback releases the reservation

#### Scenario: Websocket health-error settlement precedes the health write

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **WHEN** the finalizer settles the reservation
- **THEN** it waits for the settlement to commit before recording the account-health error

#### Scenario: Websocket health waits for fallback settlement

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **AND** its primary settlement fails
- **WHEN** fallback release remains in progress
- **THEN** the finalizer does not record the account-health error
- **AND** it records the error only after fallback release commits

#### Scenario: Unconfirmed websocket settlement leaves health unapplied

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **WHEN** both primary settlement and fallback release fail
- **THEN** the finalizer does not record the account-health error
- **AND** the upstream connection is still scheduled for reconnect and retirement

#### Scenario: Unconfirmed retry settlement drops deferred health

- **GIVEN** a keyed stream retry has deferred an account-health penalty until replacement selection
- **WHEN** neither primary settlement nor fallback release confirms settlement
- **THEN** the deferred penalty and any immediately following terminal health write remain unapplied
- **AND** the retry path does not start a second settlement for the transferred reservation

#### Scenario: Keyed pre-created retry defers the health write

- **GIVEN** a keyed HTTP-bridge request whose reservation is unsettled
- **WHEN** a retryable pre-created failure (model capacity, owner-pinned quota, or another retryable error) is handled
- **THEN** no load-balancer health write occurs before settlement
- **AND** the classified penalty is queued on the request state
- **AND** an equivalent unkeyed request keeps the immediate health write

#### Scenario: Deferred pre-created penalty applies after settlement commits

- **GIVEN** a keyed HTTP-bridge request with a queued pre-created health penalty
- **WHEN** its reservation settlement or fallback release commits
- **THEN** the queued penalty is applied after the commit
- **AND** each queued entry is applied exactly once

#### Scenario: Failed deferred health write does not abort finalization

- **GIVEN** a committed settlement with a queued pre-created health penalty
- **WHEN** the deferred health write fails
- **THEN** the failure is logged and the penalty is dropped
- **AND** the remaining terminal finalization continues
- **AND** a deferred account-backoff failure does not prevent the deferred health drain

#### Scenario: Shutdown drains pending settlements

- **WHEN** the service shuts down gracefully with settlements in flight
- **THEN** shutdown waits for them up to the configured drain timeout
- **AND** a pending ordering-sensitive fallback release remains part of that drain despite cancellation before primary startup or during fallback
