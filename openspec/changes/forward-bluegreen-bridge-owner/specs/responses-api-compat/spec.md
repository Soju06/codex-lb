## ADDED Requirements

### Requirement: Rolling deploys MUST NOT expose bridge owner mismatch to reachable sessions

During a rolling or blue-green deployment overlap, a Responses HTTP bridge request for a session owned by a different live ring member MUST be forwarded to the owning member when that owner endpoint can be resolved. The proxy MUST NOT expose `409 bridge_instance_mismatch` to the client in that reachable-owner case.

#### Scenario: Request arrives at the replacement while the owner is retained

- **GIVEN** a durable HTTP bridge session owned by instance B
- **AND** a request for that session arrives at instance A
- **AND** both instances are active bridge ring members
- **AND** instance B's endpoint resolves from ring membership
- **WHEN** instance A creates or locates the bridge session
- **THEN** instance A returns an owner-forward dispatch
- **AND** it does not attempt to claim B's live durable lease
- **AND** it does not return `409 bridge_instance_mismatch`
