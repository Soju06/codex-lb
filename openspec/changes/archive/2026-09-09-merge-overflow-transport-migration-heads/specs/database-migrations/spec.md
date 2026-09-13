## ADDED Requirements

### Requirement: Overflow and transport migration heads converge without rewriting history

The migration graph MUST join `20260908_000000_add_subscription_overflow` and
`20260908_000000_replace_upstream_stream_transport_default_sentinel` through a
new merge revision. Both existing revisions MUST remain unchanged. The merge
revision's upgrade and downgrade MUST NOT execute application schema or data
operations.

#### Scenario: An existing parent upgrades to the merged head

- **GIVEN** a populated database at either parent, or at both parents
- **WHEN** the normal migration runner upgrades to `head`
- **THEN** it MUST apply any missing parent according to that parent's existing
  behavior and finish at the single merge head
- **AND** it MUST preserve existing application rows except for data changes
  already required by a missing parent's migration
- **AND** the resulting schema MUST match the current ORM metadata

#### Scenario: Downgrading only the merge preserves both parents

- **GIVEN** a populated database at the merge revision
- **WHEN** Alembic downgrades to either immediate parent
- **THEN** it MUST undo only the merge revision and retain both parent revision
  stamps and both parent schemas
- **AND** application data MUST remain unchanged
- **AND** upgrading to `head` again MUST restore the single merge stamp without
  repeating either parent's schema or data operations
