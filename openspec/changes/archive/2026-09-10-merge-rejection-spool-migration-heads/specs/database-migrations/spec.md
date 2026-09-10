## ADDED Requirements

### Requirement: Rejection and spool retention histories converge

The migration graph MUST join `20260910_010000_add_account_rejection_generation` and `20260910_010000_dashboard_spool_retention` with a new merge revision. Both parent files MUST remain unchanged. The merge upgrade and downgrade MUST perform no application schema or data operations.

#### Scenario: Upgrade from either populated parent

- **GIVEN** a populated database at either parent or both parents
- **WHEN** `codex-lb-db upgrade head` runs
- **THEN** it MUST finish at one head after applying any missing parent
- **AND** existing data MUST remain unchanged except for defaults required by the missing parent
- **AND** `codex-lb-db check` MUST report no schema drift or migration policy violations

#### Scenario: Downgrade only the merge

- **GIVEN** a populated database at the merge revision
- **WHEN** Alembic downgrades to either immediate parent
- **THEN** both parent revision stamps, schemas and application data MUST remain intact
- **AND** upgrading to `head` again MUST restore the single merge stamp
