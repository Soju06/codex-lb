## MODIFIED Requirements

### Requirement: Request-log archive lookup schema
The database schema SHALL preserve a nullable archive lookup id on request logs so dashboard archive lookups can remain distinct from response-id continuity lookup.

#### Scenario: Request-log archive lookup column exists after migration
- **WHEN** migrations run to head
- **THEN** `request_logs` contains a nullable `archive_request_id` column
- **AND** existing request-log rows without the column value remain valid
