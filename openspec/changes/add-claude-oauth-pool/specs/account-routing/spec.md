# account-routing Specification (delta)

## ADDED Requirements

### Requirement: Provider-discriminated account pool

The proxy account selector SHALL filter candidates by a `provider` value (`'codex'` or `'claude'`) before applying the existing eligibility, health-tier, model-plan, quota, cooldown, circuit-breaker, and budget-safety gates. Accounts whose `provider` does not match the requested provider SHALL NOT be selected.

#### Scenario: Codex provider filter excludes Claude accounts

- **GIVEN** the pool contains at least one Claude account and at least one Codex account
- **WHEN** the proxy handles a `/v1/*` request that resolves provider=`codex`
- **THEN** account selection only considers accounts with `provider='codex'`

#### Scenario: Claude provider filter excludes Codex accounts

- **GIVEN** the pool contains at least one Claude account and at least one Codex account
- **WHEN** the proxy handles a `/claude/v1/*` request that resolves provider=`claude`
- **THEN** account selection only considers accounts with `provider='claude'`

#### Scenario: Provider filter returns no candidate

- **GIVEN** the pool contains only Codex accounts
- **WHEN** the proxy handles a `/claude/v1/*` request that resolves provider=`claude`
- **THEN** the proxy returns 503 with an OpenAI-compatible error envelope indicating no Claude accounts are available

### Requirement: Claude rate-limit cooldown mirrors Codex cooldown

The proxy account selector SHALL treat an Anthropic upstream `429` response (or `anthropic-ratelimit-status: rejected`) on a Claude account the same way a Codex `429` is treated: the account SHALL be placed in cooldown by setting `accounts.status = AccountStatus.RATE_LIMITED` and `accounts.reset_at` to a future unix timestamp. The account SHALL NOT be selected while in cooldown.

#### Scenario: Anthropic 429 sets Claude account cooldown

- **GIVEN** a healthy Claude account just returned `429` from upstream
- **WHEN** the proxy records the response
- **THEN** `accounts.status` reflects `RATE_LIMITED`
- **AND** `accounts.reset_at` is set to a future timestamp
- **AND** the account is not selected for subsequent Claude requests until reset_at passes

#### Scenario: Anthropic 200 clears stale cooldown

- **GIVEN** a Claude account's previous `reset_at` has passed
- **WHEN** the proxy records a successful 200 response
- **THEN** the account status returns to active
- **AND** the account becomes eligible for selection again

## MODIFIED Requirements

### Requirement: Account identity is provider-aware

The system SHALL identify an account by the tuple `(provider, identity)` where `identity` is `email` for Codex accounts and `claude_account_uuid` for Claude accounts. The accounts table SHALL have a partial unique index on `claude_account_uuid` for rows where `provider='claude'`, and SHALL preserve the existing unique constraint on `email` for rows where `provider='codex'`. The `accounts.email` column SHALL become nullable so that Claude accounts without an email claim from Anthropic OAuth can still be persisted.

#### Scenario: Two Claude accounts with the same email are allowed when uuid differs

- **GIVEN** two separate Claude subscriptions both report the same email
- **WHEN** the operator adds both via the dashboard
- **THEN** both rows are persisted because the partial unique index is keyed on `claude_account_uuid`, not on `email`

#### Scenario: Two Codex accounts with the same email are rejected

- **GIVEN** two Codex accounts both report the same email
- **WHEN** the operator attempts to add the second
- **THEN** the unique-email constraint rejects the second add with 409
