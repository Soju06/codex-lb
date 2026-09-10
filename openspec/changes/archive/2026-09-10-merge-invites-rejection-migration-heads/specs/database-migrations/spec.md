## ADDED Requirements

### Requirement: Invite and rejection migration histories converge

The migration graph MUST join `20260910_200000_merge_dashboard_users_rejection_heads` and `20260909_040000_add_dashboard_user_invites` through a new merge revision. All published migration files MUST remain unchanged. The merge upgrade and downgrade MUST perform no application schema or data operations.

#### Scenario: Upgrade populated invite or rejection history

- **GIVEN** a populated database at either immediate parent or both parent stamps
- **WHEN** `codex-lb-db upgrade head` runs
- **THEN** it MUST apply only the missing history and finish at one head
- **AND** existing invites, roles/grants, users/identities, credentials, session generations, API-key ownership, audit rows and rejection/retention evidence MUST remain intact except defaults required by missing historical migrations
- **AND** `codex-lb-db check` MUST report no policy violation or schema drift

#### Scenario: Undo only the invite join

- **GIVEN** a populated database at the new merge
- **WHEN** Alembic downgrades to either immediate parent
- **THEN** both parent stamps and all application rows/schemas MUST remain intact
- **AND** public reupgrade/check MUST restore one stamp without changing those rows/schemas
