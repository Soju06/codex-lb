## ADDED Requirements

### Requirement: File finalize uses durable replica-shared ownership

When `POST /backend-api/files/{file_id}/uploaded` references a live durable file pin, the service MUST resolve that pin from the shared database and route finalization only through the owning account. The owner decision MUST NOT use a process-local cache. Expiry, reclaim, and cleanup MUST use database-authoritative time. If durable owner resolution fails, the service MUST fail closed before selecting or invoking an unpinned fallback account.

#### Scenario: another replica finalizes through the durable owner

- **GIVEN** one replica registered `file_xyz` through `account_a`
- **WHEN** another replica handles `POST /backend-api/files/file_xyz/uploaded`
- **THEN** it MUST resolve the shared durable pin
- **AND** it MUST finalize only through `account_a`

#### Scenario: finalize owner lookup failure does not fall back

- **GIVEN** `file_xyz` requires a durable owner decision
- **WHEN** the shared database lookup fails
- **THEN** finalization MUST fail before any unpinned account selection or upstream invocation

#### Scenario: an expired identifier can be reclaimed using database time

- **GIVEN** the durable pin for `file_xyz` has expired according to the database clock
- **WHEN** a later upload claims `file_xyz` through `account_b`
- **THEN** the durable owner MUST become `account_b`
- **AND** every replica's next finalize decision MUST resolve `account_b`
