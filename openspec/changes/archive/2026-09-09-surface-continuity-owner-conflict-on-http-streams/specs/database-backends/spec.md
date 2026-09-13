## ADDED Requirements

### Requirement: Account ChatGPT identity lookups are index-supported

Deployments MUST maintain an index (`idx_accounts_chatgpt_account_id`) on
`accounts (chatgpt_account_id)` so that per-snapshot ChatGPT account identity
lookups performed during live usage snapshot settlement do not scan the
accounts heap. On PostgreSQL the migration MUST build the index concurrently
and MUST complete without failing when a valid index of the same name already
exists.

#### Scenario: Identity lookup is index-supported after migration

- **WHEN** database migrations are applied
- **THEN** the `accounts` table includes an index on `chatgpt_account_id` named `idx_accounts_chatgpt_account_id`
- **AND** live usage settlement's unique ChatGPT identity lookup is satisfiable by that index for its filter phase

#### Scenario: Interrupted concurrent build is repaired, not accepted

- **GIVEN** the database backend is PostgreSQL
- **AND** a previous `CREATE INDEX CONCURRENTLY` for `idx_accounts_chatgpt_account_id` was interrupted, leaving an invalid index (`pg_index.indisvalid = false`) under the same name
- **WHEN** the schema migration is applied
- **THEN** the migration MUST drop the invalid index and rebuild it rather than accepting it via `IF NOT EXISTS`
