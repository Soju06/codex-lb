## ADDED Requirements

### Requirement: Upstream quota recovery merges with local fallback policy

The upstream usage refresh and quota recovery changes MUST be merged so stale or missing quota status can recover, while local fallback and budget-safe routing semantics remain explicit.

#### Scenario: Primary budget-safe gate uses primary usage only

- **WHEN** routing evaluates whether an account is safe for primary traffic
- **THEN** the merged budget-safe gate uses the upstream primary-usage semantics
- **AND** local fallback-only eligibility is not accidentally promoted to primary eligibility

#### Scenario: Quota status recovers after refresh

- **GIVEN** an account has stale or missing quota status
- **WHEN** usage refresh succeeds
- **THEN** the merged service updates quota status and dashboard quota display fields coherently
