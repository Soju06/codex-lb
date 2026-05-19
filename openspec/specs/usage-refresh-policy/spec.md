# usage-refresh-policy Specification

## Purpose
Define how background usage refresh reacts to auth-like failures without permanently hammering bad accounts.
## Requirements
### Requirement: Usage refresh cools down repeated auth-like failures

Background usage refresh MUST apply a cooldown to accounts that repeatedly fail usage refresh with ambiguous `401` or `403` responses. Accounts in that cooldown window MUST be skipped until the cooldown expires or a later successful refresh clears it.

#### Scenario: Ambiguous usage 401 enters cooldown
- **WHEN** usage refresh receives a `401` that does not match a permanent deactivation signal
- **THEN** the account is not deactivated immediately
- **AND** subsequent refresh cycles skip the account until the cooldown window expires

#### Scenario: Successful refresh clears cooldown
- **WHEN** a later usage refresh succeeds for an account that had been cooled down
- **THEN** the cooldown is cleared
- **AND** normal refresh cadence resumes

### Requirement: Usage refresh deactivates on clear deactivation signals

The system MUST deactivate accounts when usage refresh receives a permanent deactivation signal. At minimum, `402`, `404`, and `401` responses whose message explicitly indicates that the OpenAI account has been deactivated MUST be treated as deactivation signals.

#### Scenario: Usage 401 deactivation message deactivates the account
- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream message states that the OpenAI account has been deactivated
- **THEN** the account is marked `deactivated`
- **AND** later usage refresh cycles skip that account

### Requirement: token_expired at the refresh boundary deactivates the account

When the OAuth refresh endpoint fails with error code `token_expired`, the system MUST treat it as a permanent authentication failure on par with `refresh_token_expired` / `refresh_token_reused` / `refresh_token_invalidated`. The affected account MUST be deactivated and removed from the routing pool until it is re-authenticated.

#### Scenario: Refresh-time `token_expired` is classified as permanent

- **WHEN** `classify_refresh_error("token_expired")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `token_expired` deactivates the account

- **WHEN** `AuthManager.refresh_account` receives a `RefreshError("token_expired", ..., is_permanent=True)` from `refresh_access_token`
- **THEN** the account is transitioned to `DEACTIVATED`
- **AND** the deactivation reason references the re-login requirement so the dashboard can surface it
- **AND** the account is no longer selected by the load balancer until it is re-authenticated

#### Scenario: Usage-refresh-time `token_expired` deactivates the account

- **WHEN** background usage refresh observes an upstream error whose code is `token_expired` (via `_should_deactivate_for_usage_error`'s permanent-code check)
- **THEN** the account is transitioned to `DEACTIVATED` immediately, without entering the ambiguous-401 cooldown loop

### Requirement: Usage capacity recognizes upstream ChatGPT plan types

The system MUST recognize account plan types returned by upstream ChatGPT auth and usage payloads when calculating absolute usage capacity. `prolite` MUST be treated as a supported account plan with Plus x5 capacity values (`1125.0` primary and `37800.0` secondary), while preserving the stored plan type value for display and request-log context.

#### Scenario: Pro Lite account contributes aggregate remaining credits

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows report `used_percent` below 100
- **WHEN** the system builds usage window summaries or per-account remaining credit values
- **THEN** the account contributes `1125.0` primary capacity and `37800.0` secondary capacity
- **AND** the computed remaining credits are non-zero according to the reported usage percent

### Requirement: Pro Lite accounts are eligible for Pro-gated models

The system MUST treat stored `prolite` account plan types as Pro-equivalent when evaluating model registry plan eligibility, while preserving the stored `prolite` value for display and request-log context.

#### Scenario: Pro Lite account can be selected for a Pro-gated model

- **GIVEN** an active account whose stored `plan_type` is `prolite`
- **AND** its latest primary and secondary usage rows are below the configured usage threshold
- **AND** the requested model is allowed for `pro` accounts by the model registry
- **WHEN** proxy account selection evaluates eligible accounts for the requested model
- **THEN** the Pro Lite account remains eligible for selection
- **AND** the selection does not fail with `no_accounts`

