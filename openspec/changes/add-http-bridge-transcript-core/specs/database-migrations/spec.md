## ADDED Requirements

### Requirement: HTTP bridge operations have additive transcript-core storage

The `http_bridge_operations` table MUST add nullable JSON text columns
`response_output_items_json` and `response_replay_input_json`, plus non-null
`transcript_version`, `response_output_items_complete`,
`response_replay_input_complete`, and `response_replay_input_turn_count`
columns with defaults of `0`, `false`, `false`, and `0` respectively. The
revision MUST be additive and safe for existing rows.

#### Scenario: Existing operations survive the schema expansion

- **WHEN** the transcript-core migration runs on an existing operation table
- **THEN** all existing rows remain readable
- **AND** the new non-null columns contain their conservative defaults
- **AND** the JSON columns remain nullable

### Requirement: Transcript-core recovery indexes are present

The migration MUST add an index on
`http_bridge_operations(session_id, state, created_at)` and an index on
`http_bridge_operations(response_id, state)`. Re-running the migration MUST
not fail when either index already exists.

#### Scenario: Upgrade and downgrade manage both indexes

- **WHEN** the migration upgrades an existing operation table
- **THEN** both named indexes exist
- **WHEN** the migration downgrades to its parent
- **THEN** both indexes and all six transcript-core columns are removed
