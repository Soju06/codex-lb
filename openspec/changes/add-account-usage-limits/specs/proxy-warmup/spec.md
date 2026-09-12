## MODIFIED Requirements

### Requirement: Warmup mode semantics are deterministic

The endpoint SHALL implement three warmup modes with deterministic behavior:

- `normal`: submit warmup only for accounts that have a primary (5h) usage row, 100% remaining primary usage, and no blocking account usage limit.
- `strict`: if any target account fails the same eligibility checks, reject the entire request and submit no warmups.
- `force`: bypass the primary-window usage check, but not an enabled account usage limit in `reached` or `data_unavailable` state.

An account SHALL be considered eligible for `normal` and `strict` only when:

- a primary usage row exists,
- `window_minutes=300`,
- remaining usage is 100% (used percent is 0), and
- its account usage limit is disabled or `available`.

#### Scenario: Normal mode skips ineligible accounts

- **WHEN** a `normal` warmup request includes eligible and ineligible accounts
- **THEN** only eligible accounts are submitted and ineligible accounts are returned as skipped

#### Scenario: All-or-none rejects mixed eligibility pool

- **WHEN** a `strict` warmup request includes any ineligible account
- **THEN** the system rejects the request and submits zero warmup upstream requests

#### Scenario: Force bypasses usage eligibility

- **GIVEN** a `force` request includes an account that fails the primary-window usage check
- **AND** the account's usage-limit policy is disabled or `available`
- **WHEN** warmup eligibility is evaluated
- **THEN** the system bypasses the primary-window check and submits a warmup request for that account

#### Scenario: Force bypasses primary-window eligibility, not account policy

- **GIVEN** a `force` request includes an account that fails the primary-window usage check
- **AND** the account's enabled usage limit is `reached` or `data_unavailable`
- **WHEN** warmup eligibility is evaluated
- **THEN** the system bypasses the primary-window check but skips that account with reason `account_usage_limit_reached`
- **AND** no warmup request is submitted for that account

## ADDED Requirements

### Requirement: Warmup submissions reauthorize account usage limits before dispatch

After credential refresh and before each compact request is dispatched, the endpoint MUST atomically reload the target account's current policy and standard primary, secondary, and monthly observations, MUST require the account to remain `active`, and MUST reapply the canonical usage-limit evaluator. A missing account MUST fail that target with code `account_not_found`; a non-active account MUST fail it with code `account_not_active`; a `reached` or `data_unavailable` policy MUST fail it with code `account_usage_limit_reached`; and a final authorization read failure MUST fail it with code `account_usage_limit_authorization_failed`. None of these outcomes may send the compact request upstream.

#### Scenario: Account reaches the limit after warmup planning

- **GIVEN** `/v1/warmup` selected an account while its enabled policy was available
- **AND** a newer standard observation reaches the maximum before per-account submission
- **WHEN** final warmup authorization reloads the account policy and observations
- **THEN** that target fails with code `account_usage_limit_reached`
- **AND** no compact request is sent upstream
