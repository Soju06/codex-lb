## ADDED Requirements

### Requirement: Sticky owner admission and retry metadata

Sticky selection MUST preserve upstream exhaustion reset metadata for a hard owner. A soft selection that allows cap spillover MUST exclude a capped account whose local cooldown expires between cap evaluation and the draw. A rejected recovery probe MUST release provisional admission and restore its still-current provisional affinity even if the caller is cancelled.

#### Scenario: Hard owner is exhausted by the provider

- **WHEN** hard-owner selection returns upstream exhaustion with a reset
- **THEN** the selection result preserves that reset

#### Scenario: A rejected recovery probe is cancelled during cleanup

- **WHEN** a recovery probe wrote provisional affinity but cannot commit its reservation
- **AND** the request is cancelled while releasing the lease
- **THEN** the lease and reservation are released and any still-current provisional affinity is restored

#### Scenario: Another request writes affinity to the same account

- **WHEN** an independent request writes a binding to the same account before a cancelled probe writes or after its provisional write
- **THEN** probe cleanup keeps the independent binding

#### Scenario: A capped soft owner becomes routable

- **WHEN** a capped soft owner's cooldown expires during selection
- **AND** cap spillover is permitted and an uncapped sibling can serve the request
- **THEN** the capped owner is excluded from the draw

### Requirement: Pinned WebSocket denial and expired-frame ownership

When required-owner selection returns a local usage-limit denial, the WebSocket MUST forward the denial for that owner without selecting a sibling. The WebSocket MUST publish finalization ownership for an expired pending frame before cancellation can strand the removed frame.

#### Scenario: Pinned WebSocket owner reaches its reserve

- **WHEN** a previous-response WebSocket request requires an owner whose reserve blocks admission
- **THEN** it receives the local usage-limit denial and no sibling receives the request

#### Scenario: Cancellation during expired-frame handoff

- **WHEN** the WebSocket removes an expired pending frame
- **AND** the caller is cancelled as ownership transfers
- **THEN** a registered finalization task remains responsible for its settlement

### Requirement: Capped historical usage samples

Capped historical usage reads MUST exclude no-data placeholders before applying the row cap.

#### Scenario: Newest history row has no measurement

- **GIVEN** a placeholder is newer than a measured row in the same window
- **WHEN** the history read permits only one row
- **THEN** it returns the measured row
