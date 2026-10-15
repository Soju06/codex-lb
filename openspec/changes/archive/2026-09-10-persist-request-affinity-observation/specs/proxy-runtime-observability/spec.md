## ADDED Requirements

### Requirement: Durable affinity observation on request logs

The system MUST persist `sticky_key_source`, `sticky_kind`, and `sticky_key_hash` on existing Responses and compact request-log rows independently of trace settings. Source MUST describe the existing resolved source classification. Kind and hash MUST describe the resolved affinity policy associated with the logged attempt or final request state. The hash MUST be the first 16 lowercase hexadecimal characters of SHA-256 over the resolved key's UTF-8 bytes. Observation MUST NOT rederive keys, change routing, health, retries, settlement ordering, callback ownership, or persistence drain semantics.

#### Scenario: Resolved affinity is recorded without trace
- **WHEN** an HTTP or native WebSocket Responses request resolves affinity and emits a request-log row with tracing disabled
- **THEN** the row stores the source, kind, and hash from that decision
- **AND** the authorized request-log listing returns `stickyKeySource`, `stickyKind`, and `stickyKeyHash`
- **AND** these fields contain no raw keys or prompt content

#### Scenario: Stable grouping across requests
- **WHEN** two requests use the same resolved key
- **THEN** their stored hashes match
- **AND** different synthetic keys with distinct SHA-256 prefixes produce different hashes

#### Scenario: Failure and retry rows retain their meaning
- **WHEN** an existing emitter records an attempt failure or final settlement row
- **THEN** the row records the affinity associated with that attempt or final request state
- **AND** no extra rows are added to simulate an attempt history
- **AND** a final row that covers multiple sends does not claim to enumerate intermediate decisions

#### Scenario: Affinity is absent or unavailable
- **WHEN** resolution explicitly finds no affinity
- **THEN** the source is `none` and kind and hash are null
- **WHEN** the policy has no key after an existing routing adjustment
- **THEN** the hash is null and source retains its original resolved classification
- **AND** kind equals the adjusted policy kind: null when recovery clears it, or `codex_session` when only a broad session key is ignored
- **WHEN** a row predates the migration or no affinity observation was available to its emitter
- **THEN** all three fields are null without an invented backfill

#### Scenario: Existing privacy and retention apply
- **WHEN** request logs are read, retained, or deleted
- **THEN** affinity metadata follows the same access controls and retention lifecycle as its owning row
- **AND** request-log responses without `conversations:read` permission return null for the three affinity fields
- **AND** enabling raw-key tracing does not make these columns store raw keys

#### Scenario: Upgrade and downgrade preserve existing records
- **WHEN** a populated database upgrades to the affinity metadata revision
- **THEN** existing request and ownership records remain intact and historical affinity metadata is null
- **WHEN** that revision is downgraded
- **THEN** only the three new columns are removed and existing record values remain intact
