## ADDED Requirements

### Requirement: Live DRAINING durable leases reject foreign claims

When a durable HTTP-bridge session is `DRAINING` and another instance still holds an unexpired lease, a foreign `claim_live_session` MUST leave the current owner and lease unchanged even when `allow_takeover` is true. Local session create MUST use the same live-owner predicate as turn-state takeover and MUST NOT treat `DRAINING` alone, or a forced recovery after a missing ring endpoint, as permission to steal a live `DRAINING` lease. The locked claim row, not a stale pre-claim lookup, MUST be the source of the `DRAINING` decision. Expired, released, or `CLOSED` rows MUST remain takeover-eligible.

#### Scenario: Foreign claim refuses a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **WHEN** instance B claims the same key with `allow_takeover` false
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`
- **AND** A's lease expiry is unchanged

#### Scenario: Forced claim still refuses after an ACTIVE lookup becomes live DRAINING

- **GIVEN** instance A owns a durable session whose lookup snapshot is still `ACTIVE`
- **AND** instance B would force takeover because A's endpoint is missing
- **AND** A marks the row `DRAINING` with a live lease before B's claim lock
- **WHEN** B claims the same key with `allow_takeover` true
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`

#### Scenario: Missing owner endpoint does not force-steal a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **AND** the ring cannot resolve A's endpoint
- **WHEN** instance B creates a local HTTP-bridge session for the same key
- **THEN** the durable claim is issued with `allow_takeover` false
- **AND** A's owner and lease remain unchanged

#### Scenario: Expired DRAINING row remains takeover-eligible

- **GIVEN** a `DRAINING` durable session whose lease is expired or whose owner is released
- **WHEN** another instance claims the same key
- **THEN** that instance becomes the owner
- **AND** the row becomes `ACTIVE`
