## ADDED Requirements

### Requirement: Reset reconciliation preserves aggregate eligibility
Targeted reset updates SHALL calculate dashboard aggregate capacity using the same usage-sample eligibility as the backend summary.

#### Scenario: Another account has no usage sample
- **WHEN** one account returns to full quota while another account has plan capacity but no usage sample
- **THEN** the unsampled account SHALL NOT enter the aggregate denominator

### Requirement: Pending reset reconciliation survives polling
Account and dashboard query replacement SHALL preserve pending reset reconciliation until a snapshot fetched after the consume is available. A stale poll SHALL NOT erase pending state or overwrite a newer targeted reset summary.

#### Scenario: Periodic query during unresolved reconciliation
- **WHEN** targeted summary retries are exhausted and the ordinary query returns null reset snapshot freshness
- **THEN** the pending reset indicator SHALL remain visible without another consume

#### Scenario: Fresh periodic query resolves pending state
- **WHEN** an ordinary query returns a snapshot fetched after the consume
- **THEN** pending state SHALL clear for that account only

### Requirement: Manual reset retries survive dialog dismissal
The dashboard SHALL retain unresolved reset request identity per account in the client session across dialog dismissal and remounting. A terminal confirmed, no-reset or expired outcome SHALL release that identity for a new intentional reset.

#### Scenario: Retry after closing a failed reset dialog
- **WHEN** a consume fails and the operator closes and reopens the dialog for the same account
- **THEN** the next confirmation SHALL reuse the original request ID
- **AND** it SHALL NOT reuse that request ID for another account
