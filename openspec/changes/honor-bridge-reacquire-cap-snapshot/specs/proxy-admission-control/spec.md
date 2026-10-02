## MODIFIED Requirements

### Requirement: Cached caps govern runtime admission

New account selection, account lease acquisition, opportunistic admission, and account-cap error reporting MUST use one dashboard-settings cache snapshot obtained before entering runtime locks. These paths MUST NOT read the database or await the dashboard settings cache while holding a runtime lock.

Idle HTTP bridge stream-lease reacquisition MUST honor the effective dashboard cap for both API-key-authenticated and unkeyed sessions, including inheritance, unlimited limits, and replica partitioning. Reacquisition MUST resolve its caps, routing settings, and applicable fair-share threshold from one cache snapshot before acquiring the session pending lock. A submission whose session already owns a stream lease MUST NOT require another settings-cache read for stream-lease admission. A failed or cancelled snapshot read MUST unwind admission ownership without acquiring a lease or leaving the session lock held.

#### Scenario: Dashboard value overrides startup environment

- **GIVEN** the process environment stream cap differs from the persisted dashboard stream cap
- **WHEN** a new stream selection or lease acquisition occurs
- **THEN** the persisted cached dashboard cap controls the decision

#### Scenario: Warm turn is not refused by an obsolete startup cap

- **GIVEN** the dashboard permits 128 account streams and the startup default permits eight
- **AND** eight other streams occupy the account while a warm HTTP bridge is idle
- **WHEN** the warm bridge receives its next eligible turn
- **THEN** it acquires a stream lease and dispatches without waiting on the eight-stream startup cap
- **AND** completing that turn releases only its own lease

#### Scenario: Lowered cap still refuses warm work

- **GIVEN** the effective cached stream cap is lower than the startup value and is fully occupied
- **WHEN** a warm keyed or unkeyed bridge reacquires a lease
- **THEN** the existing account-capacity refusal applies and no upstream frame is sent
- **AND** the refused turn leaves neither a new lease nor an admission waiter behind

#### Scenario: Warm reacquisition observes updated cap semantics

- **WHEN** an idle bridge reacquires after its cached dashboard cap changes
- **THEN** an explicit zero permits unlimited streams, null inherits startup configuration, and a positive cluster cap uses the current replica share
- **AND** a previously cached per-session cap does not override the updated settings

#### Scenario: Settings failure leaves session cleanup available

- **GIVEN** a keyed or unkeyed idle bridge needs a new stream lease
- **WHEN** its settings lookup blocks, fails, or is cancelled
- **THEN** the session pending lock remains available to other work
- **AND** failure or cancellation removes the turn's admission waiter without leaking capacity

#### Scenario: Already-leased submissions remain independent of settings availability

- **GIVEN** a bridge session owns a stream lease
- **WHEN** another turn is submitted while the settings cache is unavailable
- **THEN** stream-lease admission reuses the held lease without awaiting settings
