## ADDED Requirements

### Requirement: Firewall duplicate writes return domain conflict under concurrency
The firewall allowlist create operation MUST return a domain-level duplicate conflict response for duplicate IP requests, including concurrent requests racing on the same IP.

#### Scenario: Concurrent duplicate create does not return internal server error
- **WHEN** two create operations race to insert the same normalized IP
- **THEN** one operation succeeds and the other maps to `ip_exists` conflict behavior
- **AND** the API does not surface an internal 500 due to duplicate primary key violations
