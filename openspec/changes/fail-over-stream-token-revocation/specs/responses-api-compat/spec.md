## ADDED Requirements

### Requirement: HTTP stream terminal token revocation fails over safely

The proxy MUST treat a first-event HTTP Responses `response.failed` carrying
`code=token_revoked` as a permanent account-local authentication failure. It
MUST mark the selected account `reauth_required`, exclude it from the current
request, and prevent its revoked access token from receiving new routes.

Before downstream-visible output, an otherwise unanchored first-turn request
MAY move to another eligible account after a revoked-token rejection. If its
dispatched body contains known response-owned bookkeeping, the proxy MUST
remove only bookkeeping supported by the shared replay projection and MUST
require the resulting wire body to pass the account-neutral replay gate before
moving it. A previous-response owner, input-file owner, hard turn-state owner,
or single-account route MUST NOT move across accounts. When replay cannot be
proven safe or no replacement is available, the proxy MUST preserve the
terminal authentication error rather than replace it with a preferred-account
selection error.

An account that requires reauthentication only because its refresh token is
invalid MAY remain routable while its stored access token is still usable. An
account whose access token was explicitly invalidated or revoked MUST remain
excluded until reauthentication restores it.

#### Scenario: Sticky revoked-token stream uses another account

- **GIVEN** at least two accounts are eligible for a sticky HTTP Responses
  stream
- **AND** the full-history input contains encrypted reasoning bookkeeping but
  has no previous-response, file, or turn-state owner
- **WHEN** the selected account emits a pre-visible `response.failed` with
  `code=token_revoked`
- **AND** the projected request is account-neutral
- **THEN** the selected account is marked `reauth_required` and excluded
- **AND** the request is replayed on another eligible account
- **AND** the downstream stream completes without surfacing
  `preferred_account_unavailable`

#### Scenario: Revoked access token stays out of new routing

- **GIVEN** an account is `reauth_required` because its access token was
  explicitly revoked or invalidated
- **WHEN** the proxy selects an account for a later request
- **THEN** that account is not eligible even when the stored token has no
  parseable expiration

#### Scenario: Refresh-token-only warning preserves a usable session

- **GIVEN** an account is `reauth_required` only because its refresh token was
  revoked
- **AND** its stored access token is not known to be expired
- **WHEN** the proxy selects an account
- **THEN** the account remains eligible under the existing warning-state rules

#### Scenario: Hard owner does not move after token revocation

- **GIVEN** a streaming request is pinned by previous-response, file,
  turn-state, or single-account ownership
- **WHEN** its owner emits a pre-visible `token_revoked` failure
- **THEN** the owner is marked `reauth_required`
- **AND** the request is not sent to another account
- **AND** the terminal authentication error is preserved
