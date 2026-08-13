## ADDED Requirements

### Requirement: Live DRAINING durable leases reject foreign claims

When a durable HTTP-bridge session is `DRAINING` and another instance still holds an unexpired lease, a foreign `claim_live_session` with `allow_takeover` false MUST leave the current owner and lease unchanged. Local session create MUST use the same live-owner predicate as turn-state takeover and MUST NOT treat `DRAINING` alone as permission to steal. Expired, released, or `CLOSED` rows MUST remain takeover-eligible.

#### Scenario: Foreign claim refuses a live DRAINING lease

- **GIVEN** instance A owns a durable session whose state is `DRAINING`
- **AND** A's lease is still unexpired
- **WHEN** instance B claims the same key with `allow_takeover` false
- **THEN** the row owner remains A
- **AND** the row stays `DRAINING`
- **AND** A's lease expiry is unchanged

#### Scenario: Expired DRAINING row remains takeover-eligible

- **GIVEN** a `DRAINING` durable session whose lease is expired or whose owner is released
- **WHEN** another instance claims the same key
- **THEN** that instance becomes the owner
- **AND** the row becomes `ACTIVE`
