## MODIFIED Requirements

### Requirement: token_expired at the refresh boundary deactivates the account

When the OAuth refresh endpoint fails with a credential-token error code such as
`token_expired`, `invalid_grant`, `refresh_token_expired`,
`refresh_token_reused`, or `refresh_token_invalidated`, the system MUST treat it
as a permanent refresh-token/session failure. The affected account MUST be
marked `reauth_required` and removed from the routing pool until it is
re-authenticated.

#### Scenario: Refresh-time `token_expired` is classified as permanent

- **WHEN** `classify_refresh_error("token_expired")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `invalid_grant` is classified as permanent

- **WHEN** `classify_refresh_error("invalid_grant")` is evaluated
- **THEN** it returns `True`

#### Scenario: Refresh-time `token_expired` requires re-authentication

- **WHEN** `AuthManager.refresh_account` receives a
  `RefreshError("token_expired", ..., is_permanent=True)` from
  `refresh_access_token`
- **THEN** the account is transitioned to `REAUTH_REQUIRED`
- **AND** the reason references the re-login requirement so the dashboard can
  surface it
- **AND** the account is no longer selected by the load balancer until it is
  re-authenticated

#### Scenario: Usage-refresh-time `token_expired` requires re-authentication

- **WHEN** background usage refresh observes an upstream error whose code is
  `token_expired`
- **THEN** the account is transitioned to `REAUTH_REQUIRED` immediately,
  without entering the ambiguous-401 cooldown loop

### Requirement: Usage refresh deactivates on clear deactivation signals

The system MUST deactivate accounts when usage refresh receives a permanent
account deactivation signal. At minimum, `402`, `404`, and `401` responses
whose message explicitly indicates that the OpenAI account has been deactivated
MUST be treated as deactivation signals. Credential/session invalidation codes
such as `token_invalidated` and `token_expired` MUST be marked
`reauth_required` instead of `deactivated`.

#### Scenario: Usage 401 token invalidated requires re-authentication

- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream error code is `token_invalidated`
- **THEN** the account is marked `reauth_required`
- **AND** later usage refresh cycles skip that account until re-authentication
