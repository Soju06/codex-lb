## ADDED Requirements

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
