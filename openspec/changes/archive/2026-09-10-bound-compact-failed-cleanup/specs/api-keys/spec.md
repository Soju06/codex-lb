## ADDED Requirements

### Requirement: Compact failed persistence retains durable cleanup responsibility

Compact settlement MUST retain shielded request cleanup through the existing settlement and fail-safe release attempts, including their existing bounded persistence retries. If both attempts fail, it MUST leave the durable reservation accounted for and eligible for existing stale-reservation reclamation without creating an indefinite per-request retry task or queued retry record. It MUST report `usage_settlement_failed` with unconfirmed release and MUST NOT perform account-health writes based on unconfirmed settlement. Successful settlement or confirmed fail-safe release MUST retain existing behavior. Existing stale-reclamation intervals and age thresholds MUST remain unchanged.

#### Scenario: Sustained compact persistence failure

- **GIVEN** admitted compact requests with durable reservations
- **WHEN** both settlement and fail-safe release fail for each request
- **THEN** each unresolved reservation remains reserved and counted against its key
- **AND** responses report unconfirmed settlement failure without accumulating detached retry work
- **AND** existing stale reclamation can later release eligible reservations and restore quota exactly once

#### Scenario: Cancellation during compact cleanup

- **WHEN** cancellation is active during compact settlement and fail-safe release
- **THEN** shielded cleanup attempts still run
- **AND** failed attempts do not falsely confirm release or create detached retries
