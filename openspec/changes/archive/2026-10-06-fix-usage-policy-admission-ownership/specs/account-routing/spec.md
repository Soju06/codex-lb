## ADDED Requirements

### Requirement: Selection admission survives its final persistence waits

Selection MUST observe local policy invalidation through recovery-probe commitment and process-seed persistence. An invalidated attempt MUST NOT return an account or provisional lease. Rejected probe admission MUST NOT consume the recovery quiet interval. Already committed process affinity MUST retain its owner when the attempt fails closed.

#### Scenario: Policy changes while probe commitment waits
- **GIVEN** an account has an enabled 20-percent cap and a fresh 10-percent observation
- **WHEN** its cap becomes 10 percent and local selection is invalidated while recovery-probe commitment waits
- **THEN** the invalidated account is not dispatched and its provisional admission is released
- **AND** the rejected attempt does not consume the probe quiet interval

#### Scenario: Policy changes during process-seed persistence
- **WHEN** a required owner's policy becomes blocking and local selection is invalidated during initial process-seed persistence
- **THEN** the request fails closed before upstream dispatch and provisional admission is released
- **AND** committed process affinity retains its owner

### Requirement: Pre-dispatch rejection retains cancellation cleanup ownership

Removing a rejected WebSocket frame from pending dispatch MUST retain ownership of its reservation, account lease, gate, and finalization until settlement completes. Cancellation MUST propagate without abandoning that cleanup. Late invalidated sticky selection MUST release provisional stream and token accounting before propagating cancellation while preserving committed affinity.

#### Scenario: Cancellation interrupts rejected WebSocket lease release
- **WHEN** a final owner check rejects an admitted frame and cancellation arrives while account-lease release waits
- **THEN** no upstream send occurs and the frame's lease, reservation, and gate are released
- **AND** overlapping dispatched work retains its own settlement

#### Scenario: Cancellation interrupts late sticky invalidation cleanup
- **WHEN** a policy changes during affinity persistence and cancellation interrupts provisional lease release
- **THEN** stream and token accounting return to their prior values before cancellation propagates
- **AND** committed affinity retains its owner
