## ADDED Requirements

### Requirement: Receipt and spool-retention migration branches converge without history changes

The migration graph MUST join `20260910_040000_merge_retry_claim_and_request_log_heads` and `20260910_010000_dashboard_spool_retention` through a new schema-neutral merge revision. Existing revision identifiers and parent edges MUST remain unchanged. The default head upgrade MUST succeed from an empty database or either populated parent and leave one current head matching ORM metadata.

#### Scenario: Upgrade either populated parent

- **GIVEN** a database at either parent with existing dashboard settings and retry-circuit failure evidence
- **WHEN** the operator runs `codex-lb-db upgrade head`
- **THEN** both parent schemas MUST be present and exactly one merge head MUST be current
- **AND** existing rows and populated receipt or spool-retention values MUST remain unchanged
- **AND** the migration policy and schema-drift checks MUST pass

#### Scenario: Downgrade only the merge

- **GIVEN** both parent schemas have been joined at the merge head
- **WHEN** only the merge revision is downgraded
- **THEN** both parent stamps MUST be restored without schema or row changes
- **AND** upgrading to head again MUST restore one current merge stamp without changing those rows
