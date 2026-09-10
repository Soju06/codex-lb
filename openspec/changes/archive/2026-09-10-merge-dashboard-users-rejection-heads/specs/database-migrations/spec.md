## ADDED Requirements

### Requirement: Dashboard-user and rejection migration histories converge

The migration graph MUST join `20260910_180000_merge_guest_rejection_heads` and `20260909_030000_add_audit_actor_columns` through a new merge revision. All published migration files MUST remain unchanged. The merge upgrade and downgrade MUST perform no application schema or data operations.

#### Scenario: Upgrade either populated history

- **GIVEN** a populated database at either immediate parent or both parent stamps
- **WHEN** `codex-lb-db upgrade head` runs
- **THEN** it MUST apply missing history and finish at one head
- **AND** it MUST preserve existing role rows and grants, users, identities, user and guest session generations, rejection evidence, audit rows and spool retention except changes already required by missing historical migrations
- **AND** legacy credential backfill and re-projection MUST retain their existing semantics
- **AND** `codex-lb-db check` MUST report no migration policy violation or schema drift

#### Scenario: Undo only the join

- **GIVEN** a populated database at the new merge
- **WHEN** Alembic downgrades to either immediate parent
- **THEN** both parent stamps and all application schemas and rows MUST remain intact
- **AND** public upgrade head MUST restore one stamp without changing those schemas or rows
