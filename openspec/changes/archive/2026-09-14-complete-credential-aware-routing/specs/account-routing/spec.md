## ADDED Requirements

### Requirement: Access-rejection evidence survives persistence conflicts and unrelated repairs

Refresh-token persistence-conflict handling MUST preserve an existing proven access-rejection reason while the rejected credentials remain current. A repair of account B MUST NOT suppress a successful rejection mark for account A. Repair or newer snapshot evidence for A MUST continue to fence stale marks for A.

#### Scenario: Persistence conflict meets an existing rejection
- **WHEN** guarded token persistence exhausts retries while the current row retains proven access rejection
- **THEN** the returned and persisted account retain the blocking rejection reason

#### Scenario: An unrelated account is repaired during rejection persistence
- **WHEN** B is repaired while A's guarded rejection write succeeds
- **THEN** A is immediately unavailable to stale live bridge reuse without waiting for the next invalidation poll

### Requirement: Usage and capacity consumers share credential availability

Usage refresh, reset-credit refresh and manual reset-credit eligibility, and routable dashboard capacity MUST exclude reauthentication accounts with known expired access credentials or proven access rejection. Refresh-only warning accounts whose access token is not known expired MUST retain access-based service subject to independent consumer constraints. This requirement refines older status-only exclusions for access-token-authenticated operations, including request-triggered usage refresh. This MUST NOT authorize proactive refresh-token exchange for warning accounts.

#### Scenario: Usable refresh-only warning
- **WHEN** a reauthentication warning has usable access credentials without proven rejection
- **THEN** usage and reset-credit service and capacity accounting do not exclude it solely for the warning status

#### Scenario: Rejected or expired access credentials
- **WHEN** a reauthentication account has proven rejection or known expired access credentials
- **THEN** those consumers exclude it from access-based service and routable capacity
