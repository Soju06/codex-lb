## ADDED Requirements

### Requirement: Usage-policy rejection retains cleanup ownership through partial failures

A rejected persistent-transport request MUST release its provisional admission and gate even when API-key reservation settlement fails. Failed settlement MUST retain an owner for a bounded-count cleanup retry. Websocket scope cleanup MUST keep its existing bounded wait even when rejection settlement stalls. Cancellation during bridge rejection MUST complete request cleanup, idle stream release, and required session retirement before propagating. Reader-owned requests and client error fields MUST retain their existing semantics.

#### Scenario: Websocket settlement fails once during rejection

- **WHEN** reservation release fails once after final usage-policy authorization rejects a websocket request
- **THEN** admission and gate ownership are released and reservation settlement remains eligible for cleanup retry

#### Scenario: Rejection settlement stalls during scope cancellation

- **WHEN** a rejected websocket request's initial settlement or cleanup retry stalls and the websocket scope is cancelled
- **THEN** unrelated pending-request and connection-lease cleanup proceeds after the existing bounded wait while the registered rejection task retains settlement ownership

#### Scenario: Anchored bridge rejection is cancelled during lease release

- **WHEN** an anchored continuation is rejected and cancellation arrives during create-lease release
- **THEN** stream ownership is released and required session retirement completes before cancellation propagates

### Requirement: Reserve presentation follows effective quota windows

Account summaries and reserve editors MUST expose the quota windows selected by policy evaluation, including fresh weekly-primary telemetry superseding older monthly telemetry. Dashboard primary quota constrained by a weekly reserve MUST classify the reserve-caused reduction as reserved, while provider-consumed quota remains consumed.

#### Scenario: Weekly usage supersedes old monthly telemetry

- **WHEN** fresh weekly-primary telemetry supersedes an old monthly row and reaches the weekly policy limit
- **THEN** the summary and editor expose the weekly usage and effective weekly threshold responsible for the block

#### Scenario: Weekly reserve constrains unused primary quota

- **WHEN** an unused Plus account has primary capacity 225, weekly capacity 7560, and a weekly maximum of 1 percent
- **THEN** the primary donut shows 75.6 usable credits, 149.4 reserved credits, and zero consumed credits
