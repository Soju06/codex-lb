## ADDED Requirements

### Requirement: Warmup authorization preserves concurrent operator policy edits

Reading policy and account status for warmup authorization MUST NOT write them back during subsequent claim, request-log, or usage settlement. A policy update acknowledged after that read MUST remain intact. Quota-planner warmup dispatch MUST use its resolved dashboard settings snapshot without another settings wait after final owner authorization.

#### Scenario: Operator edits policy between authorization and settlement
- **GIVEN** a reset or quota-planner warmup observes policy changes during its final owner read
- **WHEN** the operator acknowledges a newer policy before the admitted warmup settles
- **THEN** settlement preserves the newer saved policy
- **AND** later account use is governed by that policy

#### Scenario: Settings preparation precedes final authorization
- **WHEN** a quota-planner warmup resolves the dashboard settings needed for dispatch
- **THEN** final owner authorization follows that preparation
- **AND** sending uses that same settings snapshot without a new settings read

### Requirement: Committed usage remains visible after refresh cancellation

After a standard usage snapshot commits for an enabled account policy, cancellation during post-commit policy or health reads MUST NOT skip local routing invalidation. If the post-commit policy read is cancelled before the enabled state is established, invalidation MUST be conservative. Cancellation MUST propagate after invalidation, and subsequent selection MUST evaluate the committed observation.

#### Scenario: Post-commit policy read is cancelled
- **GIVEN** a fresh below-limit observation is cached for an enabled account policy
- **WHEN** a newer blocking usage observation commits and the subsequent policy read is cancelled
- **THEN** local selection inputs are invalidated
- **AND** cancellation propagates without authorizing new work from the old observation
