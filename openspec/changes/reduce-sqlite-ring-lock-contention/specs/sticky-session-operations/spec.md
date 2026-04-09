## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed and provider-scoped
The system SHALL persist each sticky-session mapping with an explicit kind and provider-scoped routing identity so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed without assuming every mapping targets a ChatGPT account.

Each persisted mapping MUST use provider scope as part of its durable identity. After the provider-scoped migration, persisted sticky mappings MUST be uniquely identified by provider scope, sticky kind, and sticky key, and each row MUST contain a non-empty `routing_subject_id`.

#### Scenario: SQLite single-instance runtime uses static bridge ring
- **WHEN** the runtime uses a SQLite database
- **AND** bridge routing is enabled without an explicit multi-instance ring configuration
- **THEN** the service uses the static single-node bridge ring derived from the local instance id
- **AND** it MUST NOT start persisted bridge-ring heartbeat writes
- **AND** HTTP bridge owner checks MUST NOT query persisted ring membership for that runtime
