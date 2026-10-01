## MODIFIED Requirements

### Requirement: Responses account selection accounts for in-flight pressure

For Responses API requests, usage-based routing MUST include immediate in-process account pressure in addition to persisted usage. Account selection MUST account for in-flight response-create work, active streams, leased token/cost estimates, recent selection pressure, account health, and configured account-local caps. Selection and lease acquisition MUST be atomic with respect to other in-process selections, and the critical section MUST NOT perform database calls, network calls, sleeps, or other blocking I/O. Every pressure term added to an account's used percent MUST be expressed in percentage points; the leased token/cost estimate term MUST be the configured lease token weight multiplied by the leased token estimate divided by the maximum per-request lease estimate, so that one maximum-size lease adds exactly the configured weight in points on every plan, and MUST NOT be scaled by plan capacity constants. Runtime pressure MUST NOT by itself take an account whose persisted usage for a window is below 100 percent to 100 percent effective usage for that window: in-flight work may lower an account's score relative to its peers but MUST NOT erase its persisted remaining capacity.

#### Scenario: Concurrent burst spreads before upstream usage refreshes

- **GIVEN** multiple eligible accounts have similar persisted usage
- **WHEN** many `/v1/responses` requests arrive concurrently before upstream usage refreshes
- **THEN** selected accounts are distributed according to immediate in-flight pressure and caps
- **AND** one account does not receive all requests solely because persisted usage was stale

#### Scenario: File-pinned bridge request does not reroute under local pressure

- **GIVEN** an HTTP bridge `/v1/responses` request references an `input_file.file_id` pinned to an upstream account
- **AND** that owner account or bridge session rejects admission with local pressure before output starts
- **WHEN** the proxy handles the admission failure
- **THEN** it returns the owner account overload instead of soft-rerouting the payload to another account
- **AND** the file-scoped request is not replayed to an account that does not own the file

#### Scenario: Runtime lock excludes blocking I/O

- **WHEN** account selection holds the balancer runtime lock
- **THEN** the implementation performs only in-memory scoring and lease mutation
- **AND** database, network, sleep, or bridge queue waits happen outside that lock

#### Scenario: One in-flight stream does not exhaust a Plus account

- **GIVEN** a Plus account with persisted weekly usage of 38 percent
- **AND** the account holds one stream lease carrying the maximum per-request token estimate
- **WHEN** usage-based routing builds the account's selection state
- **THEN** the account's effective weekly used percent is above 38 and below 100
- **AND** its relative-availability remaining credits are greater than zero

#### Scenario: Lease pressure preserves the persisted usage order

- **GIVEN** two Plus accounts with persisted weekly usage of 95 and 38 percent and equal reset times
- **AND** each holds one stream lease with the same token estimate
- **WHEN** a new unbound request is routed with `relative_availability`
- **THEN** the 38 percent account is selected
