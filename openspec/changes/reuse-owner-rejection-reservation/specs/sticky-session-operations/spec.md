## ADDED Requirements

### Requirement: Pre-dispatch owner recovery reuses the original reservation

The origin MUST reuse the original API-key reservation during pre-dispatch local recovery.
This applies when an owner-forwarded bootstrap request is rejected before upstream
dispatch and the origin enters its existing recovery path. It MUST NOT acquire
a second reservation for the same request.

The reservation lifecycle MUST still settle exactly once. A cancellation,
acknowledged owner response, or ambiguous dispatch failure MUST remain on its
existing fail-closed path and MUST NOT be treated as a local pre-dispatch
recovery.

#### Scenario: Local recovery transfers the original hold

- **GIVEN** an API-key request owns a reservation
- **AND** the HTTP bridge owner rejects the bootstrap request before dispatch
- **WHEN** the origin creates the local recovery request
- **THEN** the local request carries the original reservation identity
- **AND** no second reservation is acquired

#### Scenario: Ambiguous dispatch remains fail-closed

- **GIVEN** an API-key request owns a reservation
- **AND** owner forwarding is acknowledged or dispatch status is ambiguous
- **WHEN** forwarding fails
- **THEN** the origin does not enter local pre-dispatch recovery
