## ADDED Requirements

### Requirement: Warmup submissions reauthorize account usage limits before dispatch

After credential refresh and before each compact request is sent, the endpoint MUST reload the target account's policy, status, and standard observations in one read and MUST evaluate the usage limit again. A missing account MUST fail the target with `account_not_found`, a non-active account with `account_not_active`, a `reached` or `data_unavailable` policy with `account_usage_limit_reached`, and a failed read with `account_usage_limit_authorization_failed`. None of these outcomes MAY send the compact request.

#### Scenario: Account reaches the limit after warmup planning

- **GIVEN** `/v1/warmup` selected an account while its enabled policy was available
- **AND** a newer observation reaches the maximum before the account's submission
- **WHEN** final warmup authorization runs
- **THEN** that target fails with `account_usage_limit_reached` and no compact request is sent

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
- **WHEN** warmup eligibility is evaluated
- **THEN** the system submits a warmup for that account when its usage limit is disabled or `available`
- **AND** it skips the account with reason `account_usage_limit_reached` when its enabled usage limit is `reached` or `data_unavailable`
