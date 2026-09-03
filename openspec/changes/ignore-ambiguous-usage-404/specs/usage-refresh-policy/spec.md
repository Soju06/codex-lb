## MODIFIED Requirements

### Requirement: Usage refresh deactivates only on clear deactivation signals

The system MUST deactivate an account when usage refresh receives an unambiguous permanent
account-level signal, such as the configured payment-required status, a permanent failure code,
or an explicit account-deactivation message. Credential/session invalidation codes such as
`token_invalidated`, `token_expired`, and `app_session_terminated` MUST be marked
`reauth_required` instead of `deactivated`. An HTTP `404` without a permanent failure code or
explicit account-deactivation message MUST be treated as an ambiguous refresh failure and MUST
NOT change the account status, because it can represent an unavailable endpoint, route, or
upstream rollout affecting otherwise healthy accounts.

#### Scenario: Ambiguous usage 404 keeps an account eligible

- **GIVEN** an active account with unchanged credentials
- **WHEN** usage refresh receives HTTP `404` without a permanent failure code or explicit
  account-deactivation message
- **THEN** the account remains `active`
- **AND** the account remains eligible for later usage refresh and proxy selection
- **AND** no credential or account-status mutation is persisted

#### Scenario: Explicit deactivation remains permanent

- **WHEN** usage refresh receives an explicit account-deactivation code or message, regardless
  of whether the transport status is `401` or `404`
- **THEN** the account is marked `deactivated`
- **AND** the account is removed from proxy selection

#### Scenario: Payment required remains permanent

- **WHEN** usage refresh receives HTTP `402` Payment Required
- **THEN** the account is marked `deactivated`

#### Scenario: Usage session termination requires re-authentication

- **WHEN** usage refresh receives HTTP `401`
- **AND** the upstream error code is `app_session_terminated`
- **THEN** the account is marked `reauth_required`
- **AND** the account is removed from proxy selection until re-authentication
