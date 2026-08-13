## ADDED Requirements

### Requirement: Live DRAINING durable leases reject foreign claims

When a durable HTTP-bridge session is `DRAINING` and another instance still holds an unexpired lease, a foreign `claim_live_session` with `allow_takeover` false MUST leave the current owner and lease unchanged. Local session create MUST use the same live-owner predicate as turn-state takeover and MUST NOT treat `DRAINING` alone, or a forced recovery after a missing ring endpoint, as permission to steal a live `DRAINING` lease. Expired, released, or `CLOSED` rows MUST remain takeover-eligible.

#### Scenario: Foreign claim refuses a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **WHEN** instance B claims the same key with `allow_takeover` false
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`
- **AND** A's lease expiry is unchanged

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
