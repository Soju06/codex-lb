## MODIFIED Requirements

### Requirement: Stream reservation settlement is detached from the response path

Settling a stream API-key reservation MUST NOT block the response/stream close, with one deliberate exception: when a keyed websocket stream terminates with an account-health error, the finalizer MUST wait for the settlement to commit before the load-balancer health write (the settlement-ordering invariant), so that error path intentionally blocks on settlement. In all other cases the settlement MUST run as a tracked background task; when it fails or is cancelled, the reservation MUST still be released by the tracking fallback, and the request's finalization path MUST NOT double-release a transferred settlement. If the tracking fallback itself encounters a persistence failure, it MUST remain tracked and retry the idempotent release; no more than four retry-enabled detached fallback repository attempts may run concurrently per proxy service instance, waiting fallbacks MUST NOT open repository sessions until admitted, and persistence drain MUST NOT report completion while any retry remains unfinished. Reservations MUST continue to count toward key limits until finalized or released, so deferred settlement can never admit usage a synchronous settlement would have rejected.

#### Scenario: Response close precedes settlement completion

- **GIVEN** a keyed stream whose settlement transaction is still running
- **WHEN** the stream closes
- **THEN** the close does not wait for the settlement
- **AND** the settlement finalizes the reservation exactly once in the background

#### Scenario: Failed detached settlement still releases the reservation

- **GIVEN** a detached settlement whose finalize raises
- **WHEN** the settlement task completes
- **THEN** the tracking fallback releases the reservation

#### Scenario: Failed fallback release remains tracked

- **GIVEN** a detached settlement whose finalize raises
- **AND** the first tracking-fallback release attempt also raises
- **WHEN** persistence recovers before the drain deadline
- **THEN** the tracked fallback retries and releases the reservation exactly once
- **AND** persistence drain does not report completion before that release

#### Scenario: Concurrent fallback release retries are bounded to four

- **GIVEN** five failed detached settlements in one proxy service instance
- **WHEN** their tracking fallbacks attempt repository persistence concurrently
- **THEN** no more than four release attempts open repository sessions
- **AND** the waiting fallbacks remain tracked until they can retry

#### Scenario: Websocket health-error settlement precedes the health write

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **WHEN** the finalizer settles the reservation
- **THEN** it waits for the settlement to commit before recording the account-health error

#### Scenario: Shutdown drains pending settlements

- **WHEN** the service shuts down gracefully with settlements in flight
- **THEN** shutdown waits for them up to the configured drain timeout
- **AND** reports an incomplete drain if a tracked settlement or release remains unfinished at that timeout
