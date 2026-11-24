## ADDED Requirements
### Requirement: Native WebSocket receive failures preserve safe provenance
The relay MUST preserve an allowlisted native receive failure phase in the existing request-log failure phase when no stronger classification exists. Unknown phases and raw helper error messages MUST NOT enter the phase field. Public error codes and messages MUST remain unchanged.

#### Scenario: Consumer backpressure terminates an active stream
- **WHEN** a native WebSocket receive fails with consumer_backpressure
- **THEN** the failed request log records consumer_backpressure
- **AND** the downstream receives the existing sanitized failure envelope

#### Scenario: Unknown helper phase
- **WHEN** the helper supplies an unrecognized phase
- **THEN** the relay does not persist that value as a failure phase

#### Scenario: Stronger classification already exists
- **WHEN** a request already has a failure phase
- **THEN** transport finalization preserves that classification
