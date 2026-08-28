## ADDED Requirements

### Requirement: Unusable account transitions remove persistent affinity bindings

The system SHALL remove persistent affinity bindings when an account becomes
permanently unusable because it requires reauthentication or is deactivated.
This includes durable sticky-session mappings and durable HTTP bridge aliases.
Any durable HTTP bridge rows closed by this transition MUST clear account
ownership, owner leases, and stored continuity anchors so follow-up requests
cannot resolve stale turn-state or previous response aliases through the closed
row.

#### Scenario: Reauthentication requirement clears bridge continuity

- **GIVEN** an account has sticky-session mappings and durable HTTP bridge aliases
- **AND** a bridge row stores the latest turn state and previous response
- **WHEN** the account is marked `reauth_required`
- **THEN** sticky-session mappings for the account are deleted
- **AND** durable HTTP bridge aliases for the account's bridge rows are deleted
- **AND** the bridge rows are closed without account ownership, live owner lease, or stored continuity anchors

#### Scenario: Failed compare-and-swap status transition keeps affinity bindings

- **GIVEN** an account has sticky-session mappings and durable HTTP bridge aliases
- **WHEN** a conditional account status update does not match the expected current row state
- **THEN** the account's sticky-session mappings and durable HTTP bridge aliases remain unchanged
