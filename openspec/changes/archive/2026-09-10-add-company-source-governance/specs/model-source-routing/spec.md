## ADDED Requirements

### Requirement: Company operational admission
Company model sources SHALL derive health from persisted request outcomes. Client cancellations and ordinary request validation failures SHALL NOT count as source faults. A 429 or authentication failure SHALL cause a 60-second cooldown; three consecutive qualifying transport/server failures SHALL cause a 60-second cooldown. After cooldown expires, requests SHALL be allowed to establish recovery. Successful requests SHALL restore healthy state. Admission rejection SHALL NOT route to subscription accounts or extend cooldown.

#### Scenario: Rate limit recovery
- **WHEN** a company source returns 429
- **THEN** subsequent calls during cooldown receive a source-specific 503 with Retry-After, and calls after cooldown can reach the source

### Requirement: Local observed token budget
Company sources SHALL support an optional persisted rolling 24-hour token budget, defaulting to unlimited. Once observed input plus output tokens reach the budget, new Responses and Chat calls SHALL be rejected with 429. The dashboard SHALL distinguish this local soft budget from unknown upstream remaining quota and display missing usage records. Concurrent in-flight requests and missing upstream usage MAY exceed this soft budget.

#### Scenario: Budget exhaustion
- **WHEN** recorded tokens in the previous 24 hours reach the configured budget
- **THEN** a new request is rejected before forwarding and clearing the budget restores admission

### Requirement: Company operational visibility
The dashboard SHALL display observed health, cooldown expiry, 24-hour successes, failures, 429 and server errors, measured latency when available, and editable local token budget. Unknown health SHALL remain distinct from healthy. State SHALL survive process restart through persisted request logs and budget configuration.

#### Scenario: No observations
- **WHEN** a source has no qualifying completed requests
- **THEN** its health is unknown and its upstream quota remains unknown
