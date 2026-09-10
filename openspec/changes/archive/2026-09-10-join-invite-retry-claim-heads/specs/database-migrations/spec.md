## ADDED Requirements

### Requirement: Invite and retry-claim histories converge without rewriting published migrations

The graph MUST join `20260910_200000_merge_users_retry_claim_heads` and `20260909_040000_add_dashboard_user_invites` through a new schema-neutral merge revision. All published identifiers, parent edges and bodies MUST remain unchanged. Public head upgrade MUST converge from empty or either populated parent to one head matching ORM metadata.

#### Scenario: Upgrade either populated parent

- **GIVEN** populated account/retry or invite history with role grants, user/session and guest generations, audit rows and nondefault spool retention, plus live receipt or invite rows where their schemas exist
- **WHEN** the operator runs `codex-lb-db upgrade head`
- **THEN** all existing row fields MUST retain their values
- **AND** invite token hashes, expiry, flags, issuer snapshots and user links MUST remain intact
- **AND** newly introduced invite storage MUST be empty or newly introduced receipt columns MUST be null
- **AND** one current head and passing policy/drift checks MUST remain

#### Scenario: Downgrade only the invite join

- **GIVEN** both schemas populated with invites and a live receipt
- **WHEN** only the join is downgraded to either named immediate parent
- **THEN** both parent stamps MUST be restored with exact full schema and row preservation
- **AND** re-upgrade MUST restore one head with the same schema and rows
