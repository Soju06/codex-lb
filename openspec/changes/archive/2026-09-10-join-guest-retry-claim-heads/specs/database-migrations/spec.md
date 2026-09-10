## ADDED Requirements

### Requirement: Guest-session and receipt migration branches converge without rewriting history

The migration graph MUST join `20260910_160000_merge_retry_claim_spool_heads` and `20260908_000000_add_guest_session_generation` through a new schema-neutral merge revision. Existing revision identifiers, parent edges and bodies MUST remain unchanged. The public default head upgrade MUST succeed from an empty database or either populated parent and leave exactly one current head matching ORM metadata.

#### Scenario: Upgrade either populated parent

- **GIVEN** a database at either parent with existing retry-failure evidence and nondefault spool retention, plus a live receipt or nonzero guest-session generation where its schema exists
- **WHEN** the operator runs `codex-lb-db upgrade head`
- **THEN** all preexisting row fields MUST retain their values
- **AND** newly introduced guest-session generations MUST default to zero and newly introduced receipt fields MUST be null
- **AND** exactly one current merge head MUST remain and migration-policy and schema-drift checks MUST pass

#### Scenario: Downgrade only the guest and receipt join

- **GIVEN** both parent schemas are joined with live receipt, spool-retention and nonzero guest-session values
- **WHEN** only the join revision is downgraded to either named immediate parent
- **THEN** both parent stamps MUST be restored with exact schema and row preservation
- **AND** upgrading to head again MUST restore one merge stamp with the same schema and rows
