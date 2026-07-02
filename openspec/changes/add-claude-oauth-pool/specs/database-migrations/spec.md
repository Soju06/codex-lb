# database-migrations Specification (delta)

## ADDED Requirements

### Requirement: accounts.provider discriminator column

The `accounts` table SHALL have a `provider` column of type `TEXT NOT NULL CHECK (provider IN ('codex', 'claude'))`. Existing rows SHALL be backfilled with `provider='codex'` before the `NOT NULL` constraint is applied.

#### Scenario: Migration upgrades an existing database

- **WHEN** Alembic runs `upgrade head` on a database created before this migration
- **THEN** all existing `accounts` rows have `provider='codex'`
- **AND** the `NOT NULL` and `CHECK` constraints are present

#### Scenario: Migration downgrade restores prior schema

- **WHEN** Alembic runs `downgrade -1`
- **THEN** the `provider` column is dropped
- **AND** the prior schema is restored

#### Scenario: Inserting an invalid provider is rejected

- **WHEN** the application or a tool attempts to insert a row with `provider='openai'`
- **THEN** the database raises a CHECK constraint violation

### Requirement: accounts Claude-specific encrypted columns

The `accounts` table SHALL have the following nullable columns for `provider='claude'` rows, encrypted with the existing `app/core/crypto.py` envelope:
- `claude_account_uuid TEXT`
- `claude_refresh_token_encrypted BLOB`
- `claude_access_token_encrypted BLOB`
- `claude_access_token_expires_at DATETIME`
- `claude_scopes TEXT`
- `claude_user_email TEXT NULL`
- `claude_user_organization_uuid TEXT NULL`

A CHECK constraint SHALL require `claude_refresh_token_encrypted IS NOT NULL` whenever `provider='claude'`.

#### Scenario: Adding a Claude account persists the encrypted refresh token

- **WHEN** the dashboard submits a Claude account add with a refresh token
- **THEN** `claude_refresh_token_encrypted` is stored as ciphertext
- **AND** the plaintext refresh token is not present in the row bytes

#### Scenario: Adding a Codex account with a non-null refresh token is rejected

- **WHEN** the application attempts to insert a Codex account row with `claude_refresh_token_encrypted IS NOT NULL`
- **THEN** the database CHECK constraint rejects the insert

### Requirement: accounts Claude rate-limit cache columns

The `accounts` table SHALL have the following nullable columns populated by the proxy layer after every Claude request:
- `rate_limit_requests_remaining INTEGER`
- `rate_limit_requests_reset_at DATETIME`
- `rate_limit_input_tokens_remaining INTEGER`
- `rate_limit_input_tokens_reset_at DATETIME`
- `rate_limit_output_tokens_remaining INTEGER`
- `rate_limit_output_tokens_reset_at DATETIME`
- `rate_limit_status TEXT`

#### Scenario: Rate-limit header is persisted to the account row

- **WHEN** an Anthropic response carries `anthropic-ratelimit-requests-remaining: 42`
- **THEN** the corresponding `accounts.rate_limit_requests_remaining` column reflects 42 after the request completes

### Requirement: accounts partial unique index on (provider, claude_account_uuid)

The `accounts` table SHALL have a partial unique index over `(claude_account_uuid)` restricted to rows where `provider='claude'`. The existing `email` unique constraint for Codex accounts SHALL remain unchanged.

#### Scenario: Duplicate Claude uuid is rejected

- **WHEN** the application attempts to add a second Claude account with the same `claude_account_uuid`
- **THEN** the partial unique index rejects the insert

### Requirement: api_keys.provider_scope column

The `api_keys` table SHALL have a `provider_scope` column of type `TEXT NOT NULL` storing a comma-separated subset of `{'codex', 'claude'}`. Existing rows SHALL be backfilled with `provider_scope='codex'` before `NOT NULL` is applied.

#### Scenario: Migration upgrades an existing database

- **WHEN** Alembic runs `upgrade head` on a database created before this migration
- **THEN** all existing `api_keys` rows have `provider_scope='codex'`
- **AND** `NOT NULL` is present

#### Scenario: Migration downgrade restores prior schema

- **WHEN** Alembic runs `downgrade -1`
- **THEN** `api_keys.provider_scope` is dropped
- **AND** the prior schema is restored

### Requirement: request_logs.provider column

The `request_logs` table SHALL have a nullable `provider TEXT` column populated by the proxy layer. Existing rows SHALL remain `NULL` after the migration.

#### Scenario: Claude request logs are tagged with provider='claude'

- **WHEN** a request is handled by the Claude proxy service
- **THEN** the resulting `request_logs` row has `provider='claude'`

#### Scenario: Codex request logs continue to set provider='codex'

- **WHEN** a request is handled by the existing Codex proxy
- **THEN** the resulting `request_logs` row has `provider='codex'`

### Requirement: Single-head Alembic upgrade path

The migration introduced by this change SHALL be a single Alembic revision that sits on the current intended parent and SHALL maintain a single-head upgrade path. The change SHALL provide a `downgrade()` implementation that reverses the schema and backfilled data.

#### Scenario: Upgrade head from an empty database

- **WHEN** Alembic runs `upgrade head` against an empty database
- **THEN** all revisions apply in order without conflicts
- **AND** the resulting schema matches the ORM metadata

#### Scenario: Upgrade head from the previous head

- **WHEN** Alembic runs `upgrade head` from the prior head revision
- **THEN** this revision is the only forward path
- **AND** no merge revision is required

#### Scenario: Downgrade restores prior schema

- **WHEN** Alembic runs `downgrade -1`
- **THEN** the prior schema is restored
- **AND** no data is lost beyond what is required by the column drops