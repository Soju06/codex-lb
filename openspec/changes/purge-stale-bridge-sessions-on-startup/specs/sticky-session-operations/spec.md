## ADDED Requirements

### Requirement: Restart removes stale owned bridge state

On startup, the system MUST remove persisted HTTP bridge session rows owned by the configured bridge instance from the previous process. The cleanup MUST also remove ownerless ACTIVE/DRAINING rows with expired leases. The cleanup MUST remove associated durable bridge aliases and MUST NOT remove sticky-session mappings. The cleanup MUST NOT remove rows owned by other bridge instances.

#### Scenario: First request after restart starts without stale bridge state

- **GIVEN** the previous process left durable HTTP bridge rows owned by the configured instance
- **WHEN** the next process completes startup
- **THEN** those durable bridge rows and their aliases MUST be removed before accepting requests
- **AND** the first request MUST create fresh bridge state instead of reusing the previous process's bridge row
- **AND** sticky-session mappings MUST remain available

#### Scenario: Ownerless stale rows with expired leases are removed

- **GIVEN** durable HTTP bridge rows exist with no owner instance and expired leases
- **WHEN** the process completes startup
- **THEN** those rows and their aliases MUST be removed
- **AND** rows owned by other instances MUST NOT be removed

#### Scenario: Sticky-session mappings are preserved

- **GIVEN** sticky-session mappings exist for the account
- **WHEN** the process completes startup and purges stale bridge rows
- **THEN** sticky-session mappings MUST remain available for account affinity
