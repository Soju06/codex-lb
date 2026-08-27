## ADDED Requirements

### Requirement: Complete transcript recovery preserves snapshot descendants

When a stale Responses anchor has a complete persisted replay snapshot at an
older ancestor, the proxy MUST use that snapshot as the replay root and retain
every durable completed descendant through the requested anchor. It MUST NOT
silently skip the descendant tail. The merged transcript MUST remain within
the configured maximum turn, input-item, and byte limits.

#### Scenario: Long session recovers across a persisted snapshot

- **GIVEN** a stale anchor whose parent chain reaches a complete replay
  snapshot after more than 128 completed turns
- **AND** the snapshot and descendant tail are within the configured bounds
- **WHEN** complete transcript recovery is attempted
- **THEN** the replay starts from the snapshot root
- **AND** includes each durable descendant through the stale anchor
- **AND** preserves tool-call/output settlement without duplicate call IDs

### Requirement: Legacy replay bookkeeping remains account-neutral

Complete transcript recovery MAY remove only known response-owned legacy
reasoning, metadata, output annotations, and empty output fragments from
durable replay items before validation. Unknown fields, account-scoped state,
unsettled calls, and ambiguous shapes MUST remain fail-closed.

#### Scenario: Older persisted provider fields do not block an otherwise safe replay

- **GIVEN** a complete replay snapshot containing response-owned reasoning,
  legacy turn metadata, output annotations, or an empty tool-output fragment
- **AND** no account-scoped or duplicate-execution state is present
- **WHEN** the snapshot is rebuilt as a fresh Responses request
- **THEN** those known provider-owned fields are omitted or normalized
- **AND** the account-neutral replay validator still checks all remaining
  fields and call/output settlement

#### Scenario: Unknown replay fields remain ineligible

- **GIVEN** a durable replay item contains an unknown field or unresolved
  tool-call shape
- **WHEN** complete transcript recovery validates the replay
- **THEN** recovery fails closed without sending the replay upstream
