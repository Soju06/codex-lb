## ADDED Requirements

### Requirement: Retired identity/warmup merge stamps repair to current schema

The system MUST upgrade a database stamped at
`20260814_020000_merge_identity_and_warmup_heads` by the retired local merge
build to the current Alembic head without manual restamping. Startup or CLI
remap MAY rewrite that retired stamp to the canonical pre-repair revision, but
the subsequent upgrade MUST execute a forward repair that converges the schema
to ORM metadata. The repaired schema MUST add the file-pin,
sticky-abandonment-scope, pending-deletion, API-key reasoning-policy, and
model-source-embeddings objects current main expects, and MUST remove the
retired `idx_accounts_chatgpt_account_id` index and
`quota_planner_decisions.lease_expires_at` column if they are still present.

#### Scenario: A SQLite clone stamped at the retired merge head upgrades cleanly

- **GIVEN** a SQLite database stamped at `20260814_020000_merge_identity_and_warmup_heads`
- **AND** the schema still lacks `file_account_pins`, pending-deletion markers, API-key reasoning policy, model-source embeddings, and sticky abandonment scope
- **AND** the retired `idx_accounts_chatgpt_account_id` index and `quota_planner_decisions.lease_expires_at` column are still present
- **WHEN** startup or `python -m app.db.migrate upgrade` runs to head
- **THEN** the upgrade completes without manual stamp surgery
- **AND** `python -m app.db.migrate check` reports no schema drift
