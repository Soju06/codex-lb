## ADDED Requirements

### Requirement: Restart removes stale owned bridge state

On startup, the system MUST remove ordinary persisted HTTP bridge session rows owned by the configured bridge instance from the previous process. A recent server-namespaced account-neutral recovery row MUST instead be changed to ownerless DRAINING with an expired lease while preserving its aliases and original activity timestamp. The cleanup MUST remove ownerless ACTIVE/DRAINING rows with expired leases once their activity predates the abandoned-row retention cutoff. Deleted rows MUST lose their associated durable bridge aliases. The cleanup MUST NOT remove sticky-session mappings or rows owned by other bridge instances.

#### Scenario: First request after restart starts without stale bridge state

- **GIVEN** the previous process left ordinary durable HTTP bridge rows owned by the configured instance
- **WHEN** the next process completes startup
- **THEN** those durable bridge rows and their aliases MUST be removed before accepting requests
- **AND** the first request MUST create fresh bridge state instead of reusing the previous process's bridge row
- **AND** sticky-session mappings MUST remain available

#### Scenario: Recent verified recovery proof survives restart only until retention

- **GIVEN** the previous process left a recent server-namespaced account-neutral recovery row with task-specific aliases
- **WHEN** the next process completes startup
- **THEN** the row MUST become ownerless DRAINING with an expired lease
- **AND** its task-specific aliases and original activity timestamp MUST remain unchanged
- **AND** a later startup or abandoned-row cleanup MUST remove the row and aliases after the activity timestamp passes the retention cutoff

#### Scenario: Ownerless stale rows with expired leases are removed

- **GIVEN** durable HTTP bridge rows exist with no owner instance, expired leases, and activity older than the abandoned-row retention cutoff
- **WHEN** the process completes startup
- **THEN** those rows and their aliases MUST be removed
- **AND** rows owned by other instances MUST NOT be removed

#### Scenario: Sticky-session mappings are preserved

- **GIVEN** sticky-session mappings exist for the account
- **WHEN** the process completes startup and purges stale bridge rows
- **THEN** sticky-session mappings MUST remain available for account affinity
