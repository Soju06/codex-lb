## ADDED Requirements

### Requirement: Chunk transcript schema expands without rewriting history

The chunk transcript migration MUST add the operation format discriminator and
chunk table without rewriting existing event rows. Existing operation rows
MUST be classified as `rows_v1`, the migration graph MUST retain one canonical
head, and upgrade MUST preserve every existing transcript.

#### Scenario: Existing transcript survives upgrade

- **GIVEN** a database with a completed legacy operation and event rows
- **WHEN** it upgrades through the chunk transcript migration
- **THEN** the operation is `rows_v1`
- **AND** all legacy event rows remain unchanged

#### Scenario: New database has both transcript stores

- **WHEN** an empty database upgrades to the canonical head
- **THEN** both legacy event and chunk tables exist
- **AND** Alembic reports one head

### Requirement: Chunk schema downgrade refuses data loss

The chunk transcript migration MUST downgrade only while no chunk row and no
`chunks_v2` operation exists. If chunk-format data exists, downgrade MUST fail
before dropping the chunk table or operation format discriminator.

#### Scenario: Empty expansion downgrades safely

- **GIVEN** no chunk-format transcript has been written
- **WHEN** the migration is downgraded
- **THEN** only the additive chunk schema is removed
- **AND** legacy events remain intact

#### Scenario: Populated chunk store blocks downgrade

- **GIVEN** at least one chunk row or `chunks_v2` operation exists
- **WHEN** downgrade is requested
- **THEN** downgrade fails before destructive DDL
- **AND** all transcript data remains present
