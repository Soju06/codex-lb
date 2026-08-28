## ADDED Requirements

### Requirement: Standard usage refresh snapshots persist atomically

For one account's successful upstream usage response, the system MUST persist every available normalized standard usage window (`primary`, `secondary`, and any applicable `monthly` window) in one database transaction. All standard rows from that response MUST use the same capture timestamp. If any standard row cannot be persisted or the transaction cannot commit, the system MUST roll back the transaction so none of that response's standard rows becomes visible, and a caller-owned database session MUST remain open and reusable. This atomic unit applies to standard `usage_history` rows; additional per-model usage history and independent live-ingest writes retain their existing persistence contracts.

#### Scenario: Multi-window response commits as one snapshot

- **WHEN** a successful account usage response contains multiple normalized standard windows
- **THEN** the system persists all of those standard rows in one transaction with one shared capture timestamp

#### Scenario: Later row failure leaves no partial snapshot

- **WHEN** persistence fails after at least one standard row from an account response has been staged
- **THEN** the system rolls back the transaction and no standard row from that response is visible

#### Scenario: Caller retains its session after rollback

- **WHEN** a caller-owned session is used for a standard usage snapshot and the snapshot transaction fails
- **THEN** the repository leaves that session open and reusable after rolling back the failed transaction
