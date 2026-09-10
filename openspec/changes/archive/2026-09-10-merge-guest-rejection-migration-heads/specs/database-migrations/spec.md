## ADDED Requirements

### Requirement: Guest and rejection migration histories converge

The migration graph MUST join `20260910_160000_merge_rejection_spool_heads` and `20260908_000000_add_guest_session_generation` through a new merge revision. Existing migration files MUST remain unchanged. The new merge upgrade and downgrade MUST perform no application schema or data operations.

#### Scenario: Upgrade from either populated parent

- **GIVEN** a populated database at either parent or both parent stamps
- **WHEN** `codex-lb-db upgrade head` runs
- **THEN** it MUST apply the missing parent history and finish at one head
- **AND** it MUST preserve existing application data except defaults required by the missing history
- **AND** `codex-lb-db check` MUST report no migration policy violations or schema drift

#### Scenario: Downgrade only the guest and rejection join

- **GIVEN** a populated database at the new merge
- **WHEN** Alembic downgrades to either immediate parent
- **THEN** both parent stamps, schemas and application data MUST remain intact
- **AND** upgrading to head again MUST restore one stamp without losing rejection evidence, spool retention or guest-session generation
